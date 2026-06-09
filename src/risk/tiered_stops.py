"""
Three-tier stop system — progressive capital protection.

TIER 1 Hard stop   : ATR-based, capped at the 7% rule. Placed at entry.
TIER 2 Breakeven   : once +3%, move stop to entry — protect the principal.
TIER 3 Trailing    : once +5%, trail at 50% of gains — let winners run.
PARTIAL TAKE       : at +5%, close 50% of the position.

The ATR multiplier is adaptive (tuned weekly by the WorkflowEnhancer).
"""
from __future__ import annotations

import pandas as pd

from config.risk_limits import LIMITS
from src.agents.technical.indicators import compute_indicators
from src.utils.adaptive import get_adaptive_params


class TieredStopManager:
    BREAKEVEN_PCT = 3.0
    PARTIAL_EXIT_PCT = 5.0
    TRAILING_TRIGGER_PCT = 5.0
    TRAILING_FACTOR = 0.5

    def calculate_initial_stops(self, entry: float, df: pd.DataFrame) -> dict:
        indicators = compute_indicators(df)
        atr = indicators.get("atr_14") or (entry * 0.07)
        atr_mult = get_adaptive_params().get("atr_stop_multiplier", 2.0)

        hard_stop = entry - atr_mult * atr
        # Never looser than the 7% rule.
        max_stop = entry * (1 - LIMITS.stop_loss_pct / 100)
        hard_stop = max(hard_stop, max_stop)
        hard_stop_pct = (entry - hard_stop) / entry * 100 if entry else 0.0

        target = entry + 2 * (entry - hard_stop)  # 1:2 minimum R:R
        rr = (target - entry) / (entry - hard_stop) if (entry - hard_stop) > 0 else 0.0

        return {
            "hard_stop": round(hard_stop, 2),
            "hard_stop_pct": round(hard_stop_pct, 2),
            "breakeven_trigger_pct": self.BREAKEVEN_PCT,
            "partial_exit_pct": self.PARTIAL_EXIT_PCT,
            "partial_exit_qty_pct": 50,
            "trailing_trigger_pct": self.TRAILING_TRIGGER_PCT,
            "trailing_factor": self.TRAILING_FACTOR,
            "target": round(target, 2),
            "rr_ratio": round(rr, 2),
            "atr_used": round(atr, 2),
        }

    def update_stops_for_held_position(self, trade, current_price: float) -> dict:
        entry = trade.entry_price or 0.0
        if entry <= 0:
            return {"action": "HOLD", "new_stop": getattr(trade, "current_stop", None), "reason": ""}
        pnl_pct = (current_price - entry) / entry * 100
        current_stop = getattr(trade, "current_stop", None) or trade.stop_price or 0.0
        partial_done = bool(getattr(trade, "partial_exited", False))

        if pnl_pct >= self.PARTIAL_EXIT_PCT and not partial_done:
            return {
                "action": "PARTIAL_EXIT",
                "new_stop": round(entry, 2),  # move to breakeven simultaneously
                "reason": f"+{pnl_pct:.1f}% reached — selling 50%, stop → breakeven",
            }

        if pnl_pct >= self.BREAKEVEN_PCT and current_stop < entry:
            return {
                "action": "BREAKEVEN_STOP",
                "new_stop": round(entry, 2),
                "reason": f"+{pnl_pct:.1f}% reached — stop moved to breakeven",
            }

        if pnl_pct >= self.TRAILING_TRIGGER_PCT:
            trail = entry + self.TRAILING_FACTOR * (current_price - entry)
            if trail > current_stop:
                return {
                    "action": "TRAILING_STOP",
                    "new_stop": round(trail, 2),
                    "reason": f"Trailing: {trail:.2f} locks in {(trail - entry) / entry * 100:.1f}%",
                }

        return {"action": "HOLD", "new_stop": round(current_stop, 2), "reason": ""}
