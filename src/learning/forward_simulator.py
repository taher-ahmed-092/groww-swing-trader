"""
Forward simulator — the system's 24/7 paper-learning engine.

EVERY pipeline candidate (traded or rejected) is logged as a SimulatedTrade. 7 and
14 trading days later the scheduler fills in the actual outcome. This accumulates
learning data even in downtrends, below-threshold setups, weekends — and answers,
with real evidence, "was holding off the right call?". Pure code, no LLM.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.data.fetcher import MarketDataFetcher
from src.memory.journal import TradingJournal

log = logging.getLogger(__name__)

_REGIMES = ["BULL_TRENDING", "BEAR_TRENDING", "RANGE_BOUND", "VOLATILE",
            "TRANSITIONAL", "RECOVERY"]


def _days_since(dt: datetime) -> int:
    if dt is None:
        return 0
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).days


class ForwardSimulator:
    def __init__(self, journal: TradingJournal | None = None) -> None:
        self.journal = journal or TradingJournal()
        self.fetcher = MarketDataFetcher()

    def log_candidate(self, state: dict, decision: str = "REJECTED",
                      rejection_reason: str = "") -> None:
        tech = state.get("technical_verdict", {}) or {}
        fund = state.get("fundamental_verdict", {}) or {}
        judge = state.get("judge_verdict", {}) or {}
        try:
            self.journal.log_simulated_trade(
                symbol=state.get("symbol", ""),
                signal=tech.get("signal", "SKIP"),
                strategy_name=tech.get("strategy_name", "unknown"),
                regime=(state.get("market_context", {}) or {}).get("regime", "UNKNOWN"),
                entry_price=tech.get("entry_price", 0) or 0,
                stop_price=tech.get("stop_price", 0) or 0,
                target_price=tech.get("target_price", 0) or 0,
                fundamental_score=fund.get("score", 0) or 0,
                technical_score=tech.get("score", 0) or 0,
                judge_score=(judge.get("overall_score", 0) or 0) / 10,
                rejection_reason=rejection_reason or decision,
            )
        except Exception as exc:
            log.debug("log_candidate failed: %s", exc)

    def fill_simulation_outcomes(self) -> dict:
        updated = wins = losses = 0
        for sim in self.journal.get_simulations():
            if sim.outcome_7d is not None and sim.outcome_14d is not None:
                continue
            if not sim.entry_price:
                continue
            days = _days_since(sim.simulated_at)
            if days < 7:
                continue
            current = self.fetcher.get_current_price(sim.symbol)
            if current is None:
                continue

            changed = False
            if days >= 7 and sim.outcome_7d is None:
                sim.outcome_7d = round((current - sim.entry_price) / sim.entry_price * 100, 2)
                would_win = sim.target_price and current >= sim.target_price
                would_lose = sim.stop_price and current <= sim.stop_price
                sim.would_have_won = bool(would_win)
                updated += 1
                changed = True
                if would_win:
                    wins += 1
                elif would_lose:
                    losses += 1
            if days >= 14 and sim.outcome_14d is None:
                sim.outcome_14d = round((current - sim.entry_price) / sim.entry_price * 100, 2)
                changed = True
            if changed:
                self.journal.update_simulated_trade(sim)

        return {"updated": updated, "wins": wins, "losses": losses}

    def rejection_outcomes(self) -> dict:
        """Filled simulations grouped by rejection reason — see
        src/analytics/filter_value.py for which gates protect money and
        which only block winners."""
        from src.analytics.filter_value import rejection_reason_value

        return rejection_reason_value(self.journal.get_simulations(limit=5000))

    def get_simulation_insights(self) -> dict:
        completed = [s for s in self.journal.get_simulations() if s.would_have_won is not None]
        if len(completed) < 10:
            return {"status": "not_enough_data", "count": len(completed)}

        insights: dict = {}
        for regime in _REGIMES:
            sims = [s for s in completed if s.regime == regime]
            if len(sims) >= 3:
                wr = sum(1 for s in sims if s.would_have_won) / len(sims)
                insights[f"regime_{regime}"] = {
                    "win_rate": round(wr, 3), "count": len(sims),
                    "label": f"BUY signals in {regime}: {wr:.0%} win rate ({len(sims)} sims)",
                }

        rejected_downtrend = [s for s in completed if "DOWNTREND" in (s.rejection_reason or "")]
        if rejected_downtrend:
            correct = sum(1 for s in rejected_downtrend if not s.would_have_won)
            pct = correct / len(rejected_downtrend)
            insights["downtrend_rejection_accuracy"] = {
                "pct_correct": round(pct, 3), "count": len(rejected_downtrend),
                "label": (f"Downtrend rejections were {pct:.0%} correct "
                          f"({len(rejected_downtrend)} cases)"),
            }
        return insights
