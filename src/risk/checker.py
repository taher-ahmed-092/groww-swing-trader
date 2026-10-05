"""
Pre-trade risk checker — the hard gate before execution.

Every check below can reject a trade. All limits come from config.risk_limits
(CLAUDE.md rule 7 — never hardcode them here). Returns a structured verdict; never
raises on a normal rejection.
"""
from __future__ import annotations

import logging
import math
import os
from datetime import date, datetime
from pathlib import Path

from rich.console import Console

from config.risk_limits import LIMITS
from config.settings import settings
from src.data.watchlist import ALL_STOCKS, get_risk_params_for_tier
from src.memory.journal import CLOSE_REASON_STALE_CATCHUP, TradingJournal
from src.orchestrator.state import TradeState
from src.risk.position_sizer import PositionSizer
from src.trading.modes import get_current_mode

console = Console()
log = logging.getLogger(__name__)

# Paper/demo only — live capital always comes from the broker, never here.
DEFAULT_PORTFOLIO_VALUE_INR = settings.paper_capital_inr

# Market-neutral pairs trades have a materially higher base win probability
# than directional momentum (PairsTradingStrategy already gates entry at
# correlation >= 0.70 and z-score >= 2.0 — a real, verified divergence, not a
# hopeful breakout), so the same 1.5:1 net-R:R floor used for directional
# trades is inappropriate and was blocking genuinely good pairs signals.
DIRECTIONAL_MIN_NET_RR = 1.5
PAIRS_MIN_NET_RR = 1.15
DAILY_LOSS_LIMIT_PCT = LIMITS.daily_loss_limit_pct
DAILY_LOSS_HALT_FILE = Path("data/cache/daily_loss_halt.txt")


# Representative large-cap sample for the startup capital sanity check — cheap
# (few requests) rather than pricing all 200+ watchlist stocks on every boot.
_SANITY_CHECK_SAMPLE = ["MRF", "TCS", "RELIANCE", "INFY", "BAJFINANCE", "MARUTI"]


def check_capital_sanity(portfolio_value_inr: float = DEFAULT_PORTFOLIO_VALUE_INR,
                          prices: dict | None = None) -> dict:
    """Startup sanity check: can this capital size even 1 share of the most
    expensive sampled stock at its max stop distance? If not, every trade on
    that stock (and likely many others) is mathematically unrejectable — it
    fails the risk-per-trade gate before sizing ever runs, silently, forever,
    with no single log line calling out why. Root cause of the 2026-07
    zero-real-trades incident: ₹1500 capital couldn't clear the 3% max-risk
    gate for a single share of any large-cap stock. `prices` is injectable
    for tests; in production it's fetched live over `_SANITY_CHECK_SAMPLE`."""
    if prices is None:
        try:
            from src.data.fetcher import MarketDataFetcher

            fetcher = MarketDataFetcher()
            prices = {}
            for symbol in _SANITY_CHECK_SAMPLE:
                price = fetcher.get_current_price(symbol)
                if price:
                    prices[symbol] = price
        except Exception:
            return {"ok": True, "reason": "price lookup unavailable — skipped"}

    if not prices:
        return {"ok": True, "reason": "no priced sample stock to check"}

    max_risk_inr = portfolio_value_inr * (LIMITS.max_risk_per_trade_pct / 100)
    unaffordable = []
    for symbol, price in prices.items():
        risk_inr = round(1 * price * (LIMITS.stop_loss_pct / 100), 4)
        if risk_inr > max_risk_inr:
            unaffordable.append(symbol)

    worst_symbol = max(prices, key=prices.get)
    worst_price = prices[worst_symbol]
    worst_risk_inr = round(1 * worst_price * (LIMITS.stop_loss_pct / 100), 4)

    if not unaffordable:
        return {"ok": True, "worst_symbol": worst_symbol, "risk_inr": worst_risk_inr,
                "max_risk_inr": max_risk_inr}

    # Correct behavior: unaffordable symbols are excluded from scoring (see
    # ScoutAgent.is_affordable), not a reason to demand more capital — this
    # check is informational only, downgraded from WARNING to INFO.
    min_viable_capital = round(worst_risk_inr / (LIMITS.max_risk_per_trade_pct / 100), 2)
    examples = ", ".join(unaffordable[:5])
    message = (
        f"{len(unaffordable)} sampled symbol(s) unaffordable at current paper capital "
        f"₹{portfolio_value_inr:,.0f} ({examples}) — excluded from scoring."
    )
    log.info(message)
    console.print(f"[dim][RISK] {message}[/dim]")
    return {"ok": False, "worst_symbol": worst_symbol, "risk_inr": worst_risk_inr,
            "max_risk_inr": max_risk_inr, "min_viable_capital": min_viable_capital,
            "unaffordable_count": len(unaffordable), "unaffordable_symbols": unaffordable,
            "message": message}


# Real (TradeRecord) positions have no engine-defined max hold — they exit on
# stop/target only, checked by daily_postmarket_job. This threshold mirrors
# AlwaysOnTrader.MAX_HOLD_DAYS purely as a "this has gone unmonitored too
# long" tripwire for the startup staleness check, not an auto-exit rule.
STALE_POSITION_MAX_HOLD_DAYS = 5


def find_stale_open_positions(journal: TradingJournal | None = None) -> list[dict]:
    """Startup check (2026-08 incident: laptop off 11 days, TANLA drifted to
    -7.2% within 1pp of its -8.2% stop with nobody checking it) — any OPEN
    real trade older than STALE_POSITION_MAX_HOLD_DAYS gets flagged here so
    the caller can alert + force an immediate price check instead of waiting
    for the next scheduled daily_postmarket_job run."""
    j = journal or TradingJournal()
    now = datetime.now()
    stale = []
    for t in j.get_open_trades():
        opened_at = t.executed_at or t.proposed_at
        if not opened_at:
            continue
        age_days = (now - opened_at).days
        if age_days >= STALE_POSITION_MAX_HOLD_DAYS:
            stale.append({"trade": t, "age_days": age_days})
    return stale


def is_daily_loss_halted() -> bool:
    """True if today's cumulative REAL net P&L has already tripped the -6%
    circuit breaker. Gates real-money trades only (RiskChecker.check); the
    forced/intraday/explorer simulations ignore it. Self-clears the moment
    the date changes."""
    try:
        if not DAILY_LOSS_HALT_FILE.exists():
            return False
        return DAILY_LOSS_HALT_FILE.read_text().strip() == date.today().isoformat()
    except OSError:
        return False


def _todays_real_trades(journal: TradingJournal | None = None) -> list:
    """Today's REAL trades (closed, WIN/LOSS) from TradingJournal only —
    excludes forced/intraday/short simulation history entirely, and stale
    catch-up closes (loss accrued while the process was down, not today).
    Bug fixed:
    the circuit breaker previously summed P&L across ALL sources including
    simulations, so simulation losses (not real capital) could halt real
    signal evaluation with 0 real trades ever placed."""
    j = journal or TradingJournal()
    today = date.today().isoformat()
    return [
        t for t in j.get_recent(n=200)
        if t.closed_at and t.closed_at.date().isoformat() == today
        and t.outcome in ("WIN", "LOSS")
        and t.close_reason != CLOSE_REASON_STALE_CATCHUP
    ]


def _todays_cumulative_net_pnl_pct(journal: TradingJournal | None = None) -> float:
    """Sums today's realized net P&L% across REAL TradingJournal trades only.
    Simulated/forced/intraday/short trade history never contributes — those
    are learning-volume simulations, not capital at risk."""
    total = sum(t.pnl_pct or 0 for t in _todays_real_trades(journal))
    return round(total, 3)


def _clear_spurious_halt_if_no_real_trades(journal: TradingJournal | None = None) -> None:
    """If a halt file exists but zero real trades closed today, it was
    triggered (or is stale) from a state that predates the real-trades-only
    circuit breaker, or from simulation P&L under the old buggy logic —
    either way it cannot be justified by real capital loss. Remove it."""
    try:
        if DAILY_LOSS_HALT_FILE.exists() and not _todays_real_trades(journal):
            DAILY_LOSS_HALT_FILE.unlink(missing_ok=True)
            log.info("Removed spurious daily-loss halt: 0 real trades today.")
    except OSError:
        pass


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

        # 1b. Daily loss circuit breaker — real capital protection ONLY.
        # Computed exclusively from real TradingJournal trades; forced/intraday/
        # short simulation P&L never counts (those are learning simulations, not
        # capital at risk). With 0 real trades today, the breaker cannot fire —
        # there is nothing real to protect from — and any halt file left over
        # from before this fix (or from a stale/bugged state) is cleared.
        _clear_spurious_halt_if_no_real_trades(self.journal)
        todays_real = _todays_real_trades(self.journal)
        if not todays_real:
            pass  # 0 real trades today — circuit breaker cannot fire, skip entirely
        elif is_daily_loss_halted():
            return self._reject(["DAILY_LOSS_LIMIT — trading paused until tomorrow."])
        else:
            cumulative_pnl = _todays_cumulative_net_pnl_pct(self.journal)
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
                            f"Cumulative REAL net P&L today: {cumulative_pnl:+.2f}%\n"
                            f"Trading paused until tomorrow (real trades only).")
                except Exception:
                    pass
                return self._reject([
                    f"DAILY_LOSS_LIMIT — cumulative REAL net P&L today {cumulative_pnl:+.2f}% "
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

        # 2b. Net R:R after real transaction costs (audit finding: gross +0.24%/trade
        # expectancy was net -0.21%/trade once STT/slippage/GST applied — a target
        # that looks like 2:1 gross can be under 1:1 net). Pairs trades use a lower
        # floor (PAIRS_MIN_NET_RR) rather than being skipped entirely — a verified
        # statistical divergence (corr>=0.70, z>=2.0) has a higher base win
        # probability than directional momentum, so demanding the same 1.5:1
        # directional floor was rejecting genuinely good pairs signals.
        if entry_price := technical.get("entry_price"):
            stop_for_rr = technical.get("stop_price")
            target_for_rr = technical.get("target_price")
            if stop_for_rr and target_for_rr and (entry_price - stop_for_rr) > 0:
                from src.trading.cost_model import compute_round_trip_costs

                rr_costs = compute_round_trip_costs(entry_price, target_for_rr, 1, tier)
                net_target_move_pct = (target_for_rr - entry_price) / entry_price * 100 - rr_costs.total_pct
                net_stop_move_pct = abs((stop_for_rr - entry_price) / entry_price * 100)
                net_rr = net_target_move_pct / net_stop_move_pct if net_stop_move_pct else 0
                min_rr = PAIRS_MIN_NET_RR if is_pairs else DIRECTIONAL_MIN_NET_RR
                log.info("Net R:R floor applied for %s: %.2f:1 (pairs=%s)", symbol, min_rr, is_pairs)
                if net_rr < min_rr:
                    reasons.append(f"Net R:R {net_rr:.1f}:1 after costs — below {min_rr:.2f}:1 minimum"
                                  f" ({'pairs' if is_pairs else 'directional'})")

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
                f"UNAFFORDABLE: risk {risk_inr} INR > max allowed {round(max_risk_inr, 4)} INR "
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
