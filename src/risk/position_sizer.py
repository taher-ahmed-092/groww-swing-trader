"""
Kelly-informed, confidence-scaled dynamic position sizing.

Kelly: f = (W·R − L) / R, where W = historical win rate, L = 1−W, R = reward:risk.
We use HALF-Kelly for safety, scale by judge confidence, then apply the market-
regime size multiplier. Clamped to [₹50, ₹500]. The single biggest lever on
profitability — bet more when the edge and confidence are real, less when they aren't.
"""
from __future__ import annotations

from src.memory.journal import TradingJournal

MIN_TRADE_INR = 50.0
MAX_TRADE_INR = 500.0
MIN_TRADES_FOR_KELLY = 5
DEFAULT_WIN_RATE = 0.55


class PositionSizer:
    MIN_TRADE_INR = MIN_TRADE_INR
    MAX_TRADE_INR = MAX_TRADE_INR
    MIN_TRADES_FOR_KELLY = MIN_TRADES_FOR_KELLY
    DEFAULT_WIN_RATE = DEFAULT_WIN_RATE

    def __init__(self, journal: TradingJournal | None = None) -> None:
        self.journal = journal or TradingJournal()

    def _minimal_size(self, entry, reason) -> dict:
        return {
            "position_size_inr": self.MIN_TRADE_INR, "quantity": 0,
            "kelly_fraction": 0, "win_rate_used": 0, "confidence_multiplier": 0,
            "sizing_explanation": f"Minimum size: {reason}", "confidence_tier": "LOW",
        }

    def calculate(self, state: dict, portfolio_value: float) -> dict:
        judge_score = state.get("judge_verdict", {}).get("overall_score", 5.0) or 5.0
        tech = state.get("technical_verdict", {})
        entry = tech.get("entry_price", 0) or 0
        stop = tech.get("stop_price", 0) or 0
        target = tech.get("target_price", 0) or 0

        if entry <= 0 or stop <= 0 or target <= 0:
            return self._minimal_size(entry, "Invalid price data")

        summary = self.journal.get_performance_summary()
        n_trades = summary.get("total_closed", 0)
        win_rate = summary.get("win_rate", self.DEFAULT_WIN_RATE) \
            if n_trades >= self.MIN_TRADES_FOR_KELLY else self.DEFAULT_WIN_RATE

        rr = (target - entry) / (entry - stop) if (entry - stop) > 0 else 2.0
        rr = max(0.5, min(5.0, rr))

        kelly = (win_rate * rr - (1 - win_rate)) / rr
        kelly = max(0.0, kelly)
        half_kelly = kelly / 2.0

        # judge score 0-10 → 0.3-1.0 (even low confidence gets some size).
        confidence_mult = 0.3 + (judge_score / 10.0) * 0.7

        # Market-regime multiplier (bear/volatile shrink size).
        regime_mult = state.get("market_context", {}).get("size_multiplier", 1.0) or 1.0

        raw_size = half_kelly * confidence_mult * regime_mult * portfolio_value
        position_size = max(self.MIN_TRADE_INR, min(self.MAX_TRADE_INR, raw_size))

        quantity = int(position_size / entry) if entry > 0 else 0
        if quantity < 1 and position_size >= self.MIN_TRADE_INR:
            quantity = 1

        if judge_score >= 8.0:
            tier = "HIGH"
        elif judge_score >= 6.5:
            tier = "MEDIUM"
        else:
            tier = "LOW"

        if n_trades < self.MIN_TRADES_FOR_KELLY:
            sizing_note = f"Default sizing (only {n_trades} trades, need {self.MIN_TRADES_FOR_KELLY})"
        else:
            sizing_note = f"Kelly ({win_rate:.0%} win rate) × {confidence_mult:.0%} confidence"

        explanation = (
            f"₹{position_size:.0f} of ₹{self.MAX_TRADE_INR:.0f} max | "
            f"{tier} confidence | {sizing_note}"
        )
        if regime_mult < 1.0:
            explanation += f" | regime ×{regime_mult}"

        return {
            "position_size_inr": round(position_size, 2),
            "quantity": quantity,
            "kelly_fraction": round(half_kelly, 4),
            "win_rate_used": round(win_rate, 4),
            "confidence_multiplier": round(confidence_mult, 4),
            "sizing_explanation": explanation,
            "confidence_tier": tier,
        }
