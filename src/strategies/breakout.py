"""Breakout — buy confirmed breakouts above resistance with volume."""
from __future__ import annotations

import pandas as pd

from config.risk_limits import LIMITS
from src.strategies.base import Strategy


class BreakoutStrategy(Strategy):
    name = "breakout"
    description = "Buy confirmed breakouts above resistance with volume"
    best_regimes = ["BULL_TRENDING", "RECOVERY"]

    def generate_signal(self, df: pd.DataFrame, indicators: dict, context: dict) -> dict:
        if df is None or df.empty or len(df) < 20:
            return self._skip("insufficient data", self.name)
        entry = round(float(df["Close"].iloc[-1]), 2)

        vr = indicators.get("volume_ratio")
        atr = indicators.get("atr_14") or (entry * 0.02)
        # 20-day high excluding today (the level being broken).
        high_20 = float(df["High"].iloc[-21:-1].max()) if len(df) >= 21 else float(df["High"].max())
        year_high = float(df["High"].max())

        broke_out = entry >= high_20 or entry >= year_high * 0.999
        if not broke_out:
            return self._skip("no breakout above 20-day/52-week high", self.name, entry)
        # Volume confirmation is mandatory — avoid false breakouts.
        if vr is None or vr < 1.5:
            return self._skip(f"volume {vr} < 1.5× — false-breakout risk", self.name, entry)

        # Stop just below the breakout level; failed breakout = exit.
        stop = round(max(high_20 - atr, entry * (1 - LIMITS.stop_loss_pct / 100)), 2)
        if stop >= entry:
            stop = round(entry * (1 - LIMITS.stop_loss_pct / 100), 2)
        # Measured move: prior consolidation height added to the breakout.
        consolidation = float(df["High"].iloc[-21:-1].max() - df["Low"].iloc[-21:-1].min()) \
            if len(df) >= 21 else (entry - stop) * 2
        target = round(entry + max(consolidation, 2 * (entry - stop)), 2)

        score = round(min(1.0, 0.55 + (vr - 1.5) * 0.2), 4)  # bigger surge → higher
        return {
            "signal": "BUY", "score": score, "entry_price": entry,
            "stop_price": stop, "target_price": target,
            "rationale": f"Breakout above ₹{high_20:.0f} on {vr:.1f}× volume (measured move)",
            "strategy_name": self.name,
        }
