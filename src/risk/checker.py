"""
Pre-trade risk checker — the hard gate before execution.

Every check below can reject a trade. All limits come from config.risk_limits
(CLAUDE.md rule 7 — never hardcode them here). Returns a structured verdict; never
raises on a normal rejection.
"""
from __future__ import annotations

import math
import os
from datetime import date
from pathlib import Path

from rich.console import Console

from config.risk_limits import LIMITS
from config.settings import settings
from src.data.watchlist import ALL_STOCKS, get_risk_params_for_tier
from src.memory.journal import TradingJournal
from src.orchestrator.state import TradeState
from src.risk.position_sizer import PositionSizer
from src.trading.modes import get_current_mode

console = Console()

DEFAULT_PORTFOLIO_VALUE_INR = 1500.0
DAILY_LOSS_LIMIT_PCT = -6.0
DAILY_LOSS_HALT_FILE = Path("data/cache/daily_loss_halt.txt")


def is_daily_loss_halted() -> bool:
    """True if today's cumulative net P&L has already tripped the -6% circuit
    breaker. All trade-placing jobs (forced/intraday/real) should skip while
    this is set; it self-clears the moment the date changes."""
    try:
        if not DAILY_LOSS_HALT_FILE.exists():
            return False
        return DAILY_LOSS_HALT_FILE.read_text().strip() == date.today().isoformat()
    except OSError:
        return False


def _todays_cumulative_net_pnl_pct() -> float:
    """Sums today's realized net P&L% across ALL sources (real + forced +
    intraday + short) — the circuit breaker must see the whole picture, not
    just the real pipeline's trades."""
    from src.analytics.trade_loader import load_all_trade_history

    today = date.today().isoformat()
    total = 0.0
    for t in load_all_trade_history():
        closed_at = t.get("closed_at", "") or ""
        if closed_at[:10] == today:
            total += t.get("pnl", 0) or 0
    return round(total, 3)


class RiskChecker:
    def __init__(self, portfolio_value_inr: float = DEFAULT_PORTFOLIO_VALUE_INR,
                 open_positions: int = 0, journal: TradingJournal | None = None) -> None:
        self.portfolio_value_inr = portfolio_value_inr
        self.open_positions = open_positions
        self.journal = journal

    def _reject(self, reasons: list[str]) -> dict:
        for r in reasons:
            console.print(f"[red][RISK] REJECTED: {r}[/red]")
        return {
            "approved": False,
            "reasons": reasons,
            "position_size_inr": 0.0,
            "quantity": 0,
            "risk_inr": 0.0,
        }

    def check(self, state: TradeState) -> dict:
        reasons: list[str] = []

        technical = state.get("technical_verdict", {})
        fundamental = state.get("fundamental_verdict", {})
        judge = state.get("judge_verdict", {})

        # Market-cap tier governs position sizing (smaller = smaller positions).
        symbol = state.get("symbol", "")
        tier = ALL_STOCKS.get(symbol, {}).get("tier", "large")
        tier_params = get_risk_params_for_tier(tier)
        mode = get_current_mode()  # conserve | balanced | rogue — the risk dial

        # 1. Kill switch.
        if os.path.exists(LIMITS.kill_switch_file):
            return self._reject(["KILL_SWITCH file present — all execution halted."])

        # 1b. Daily loss circuit breaker — halts ALL sources for the rest of the day.
        if is_daily_loss_halted():
            return self._reject(["DAILY_LOSS_LIMIT — trading paused until tomorrow."])
        cumulative_pnl = _todays_cumulative_net_pnl_pct()
        if cumulative_pnl < DAILY_LOSS_LIMIT_PCT:
            try:
                DAILY_LOSS_HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
                already_alerted = (DAILY_LOSS_HALT_FILE.exists()
                                   and DAILY_LOSS_HALT_FILE.read_text().strip() == date.today().isoformat())
                DAILY_LOSS_HALT_FILE.write_text(date.today().isoformat())
                if not already_alerted:
                    from src.notifications.telegram_bot import TelegramNotifier

                    TelegramNotifier().send_message(
                        f"🚨 *DAILY LOSS LIMIT HIT*\n"
                        f"Cumulative net P&L today: {cumulative_pnl:+.2f}%\n"
                        f"Trading paused until tomorrow across all sources.")
            except Exception:
                pass
            return self._reject([
                f"DAILY_LOSS_LIMIT — cumulative net P&L today {cumulative_pnl:+.2f}% "
                f"< {DAILY_LOSS_LIMIT_PCT}% — trading paused until tomorrow."])

        # 2. Confidence floor on both legs.
        # 0.80 is the bar for real-money decisions; demo/testing uses 0.60 so the
        # execution path can actually be exercised with rule-based scores.
        # Skipped for pairs trades: technical.score there is the spread's z-score
        # confidence (not this stock's momentum quality), and fundamental.score
        # reflects only one leg of a market-neutral position — neither is what's
        # actually being bet on. PairsTradingStrategy already gates entry at
        # correlation >= 0.70 and z-score >= 2.0; the judge re-scores that spread
        # quality directly (see LLMJudge._evaluate_pairs_trade).
        is_pairs = technical.get("strategy_name") == "pairs_trading"
        min_conf = LIMITS.min_confidence
        demo = settings.effective_demo_mode
        if demo:
            min_conf = 0.60
        tech_score = technical.get("score", 0) or 0
        fund_score = fundamental.get("score", 0) or 0
        demo_tag = " (demo)" if demo else ""
        # Missing fundamental data shouldn't penalize a name in demo/learning mode —
        # use a relaxed floor (0.45) rather than rejecting for absent screener data.
        fund_min = min_conf
        if demo and fundamental.get("data_missing"):
            fund_min = 0.45
            demo_tag = " (demo, data-missing)"
        if not is_pairs and tech_score < min_conf:
            reasons.append(f"technical score {tech_score:.2f} < min {min_conf:.2f}{demo_tag}")
        if not is_pairs and fund_score < fund_min:
            reasons.append(f"fundamental score {fund_score:.2f} < min {fund_min:.2f}{demo_tag}")

        # 3. Stop must exist — no exceptions.
        stop_price = technical.get("stop_price")
        if not stop_price:
            reasons.append("no stop_price set — every trade requires a stop.")

        # 4. Judge pass. DEMO_MODE is not a real veto flag — in demo, an overall
        # score >= 6.0 counts as approved even if the verdict dict didn't set it.
        judge_approved = judge.get("approved", False)
        if not judge_approved and demo and (judge.get("overall_score", 0) or 0) >= mode.judge_threshold:
            judge_approved = True
        if LIMITS.require_judge_pass and not judge_approved:
            reasons.append("judge did not approve the trade.")

        # 5. Open position cap.
        if self.open_positions >= LIMITS.max_open_positions:
            reasons.append(
                f"open positions {self.open_positions} >= max {LIMITS.max_open_positions}"
            )

        # 5b. Sector diversification cap — never over-concentrate in one sector.
        sector = state.get("sector")
        if sector:
            try:
                journal = self.journal or TradingJournal()
                same_sector = [t for t in journal.get_open_trades() if t.sector == sector]
                if len(same_sector) >= LIMITS.max_positions_per_sector:
                    reasons.append(
                        f"Sector cap: already {len(same_sector)} open {sector} positions "
                        f"(max {LIMITS.max_positions_per_sector})"
                    )
            except Exception:
                pass

        # 6. Gut-check gate — a holistic veto beyond the checklist.
        gut = state.get("gut_check", {}) or {}
        gut_score = gut.get("gut_score", 5.0)
        if gut_score < 3.0:
            reasons.append(
                "Gut check: strong NO — "
                + (gut.get("override_reason") or gut.get("gut_reasoning", "low conviction"))
            )
        elif gut.get("modifies_decision") and gut_score < 4.0:
            reasons.append("Gut override: " + (gut.get("override_reason") or "gut vetoed"))

        # 7. Valid entry price required before sizing.
        entry_price = technical.get("entry_price")
        if not entry_price or (isinstance(entry_price, float) and math.isnan(entry_price)):
            reasons.append("no valid entry_price — cannot size position.")
            return self._reject(reasons)

        if reasons:
            return self._reject(reasons)

        # 8. Dynamic position sizing (Kelly × confidence × regime).
        sizing = PositionSizer(self.journal).calculate(state, self.portfolio_value_inr)
        quantity = sizing["quantity"]
        position_size_inr = sizing["position_size_inr"]

        # Circuit-breaker volatility: halve size on HIGH-risk names (still ≥1 share).
        cb_factor = (technical.get("circuit_breaker", {}) or {}).get("size_factor", 1.0)
        if cb_factor < 1.0 and entry_price:
            position_size_inr = round(position_size_inr * cb_factor, 2)
            quantity = max(1, int(position_size_inr / entry_price))
            sizing["position_size_inr"] = position_size_inr
            sizing["quantity"] = quantity

        # Economic-event size reduction — halve size ahead of a HIGH-impact
        # RBI/GDP/CPI release (src/data/realtime_feeds.py EconomicCalendar).
        event_flag = (state.get("market_context") or {}).get("economic_event_flag")
        if event_flag and entry_price:
            position_size_inr = round(position_size_inr * 0.5, 2)
            quantity = max(1, int(position_size_inr / entry_price))
            sizing["position_size_inr"] = position_size_inr
            sizing["quantity"] = quantity
            sizing["sizing_explanation"] = sizing.get("sizing_explanation", "") + f" | {event_flag}"
            sizing["sizing_explanation"] = (
                sizing.get("sizing_explanation", "") + f" | circuit-breaker ×{cb_factor}")

        # Tier-based sizing: mid/small caps take smaller positions (more volatile).
        tier_mult = tier_params["position_size_multiplier"]
        if tier_mult < 1.0 and entry_price:
            position_size_inr = round(position_size_inr * tier_mult, 2)
            quantity = max(1, int(position_size_inr / entry_price))
            sizing["position_size_inr"] = position_size_inr
            sizing["quantity"] = quantity
            sizing["sizing_explanation"] = (
                sizing.get("sizing_explanation", "") + f" | tier {tier} ×{tier_mult}")

        # Trading-mode sizing (conserve halves position size; rogue/balanced keep full).
        mode_mult = mode.position_size_multiplier
        if mode_mult < 1.0 and entry_price:
            position_size_inr = round(position_size_inr * mode_mult, 2)
            quantity = max(1, int(position_size_inr / entry_price))
            sizing["position_size_inr"] = position_size_inr
            sizing["quantity"] = quantity
            sizing["sizing_explanation"] = (
                sizing.get("sizing_explanation", "") + f" | mode {mode.name} ×{mode_mult}")

        if quantity < 1:
            reasons.append("dynamic sizing produced < 1 share — entry price too high for capital.")
            return self._reject(reasons)

        # 9. Portfolio risk cap (on the actual deployed quantity).
        risk_inr = round(quantity * entry_price * (LIMITS.stop_loss_pct / 100), 4)
        max_risk_inr = self.portfolio_value_inr * (LIMITS.max_risk_per_trade_pct / 100)
        if risk_inr > max_risk_inr:
            reasons.append(
                f"risk {risk_inr} INR > max allowed {round(max_risk_inr, 4)} INR "
                f"({LIMITS.max_risk_per_trade_pct}% of {self.portfolio_value_inr})"
            )
            return self._reject(reasons)

        return {
            "approved": True,
            "reasons": ["all risk checks passed"],
            "position_size_inr": position_size_inr,
            "quantity": quantity,
            "risk_inr": risk_inr,
            "tier": tier,
            "tier_size_multiplier": tier_mult,
            "trading_mode": mode.name,
            "kelly_fraction": sizing["kelly_fraction"],
            "win_rate_used": sizing["win_rate_used"],
            "confidence_multiplier": sizing["confidence_multiplier"],
            "confidence_tier": sizing["confidence_tier"],
            "sizing_explanation": sizing["sizing_explanation"],
            "sizing_details": sizing,
        }
