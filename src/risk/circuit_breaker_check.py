"""
Circuit-breaker risk — how often a stock makes violent (>4.5%) daily moves.

Frequent large moves mean wide gaps and stop-jumping; HIGH risk halves position
size and warns. Pure code over the price history. Never raises.
"""
from __future__ import annotations

import pandas as pd

_THRESHOLD = 0.045  # 4.5% daily move


class CircuitBreakerRisk:
    def assess_risk(self, symbol: str, df: pd.DataFrame | None) -> dict:
        default = {"frequency": 0, "current_near_circuit": False,
                   "risk_level": "LOW", "recommendation": "Normal sizing.",
                   "size_factor": 1.0}
        if df is None or len(df) < 20:
            return default
        try:
            window = df.tail(126)  # ~6 months of trading days
            returns = window["Close"].pct_change().abs()
            big_days = int((returns > _THRESHOLD).sum())
            months = max(1.0, len(window) / 21.0)
            per_month = big_days / months
            near = bool(returns.iloc[-1] > _THRESHOLD) if len(returns) else False

            if per_month > 2:
                level, factor, rec = "HIGH", 0.5, "Halve position — frequent circuit-level moves."
            elif per_month > 1:
                level, factor, rec = "MEDIUM", 0.75, "Reduce size — elevated volatility."
            else:
                level, factor, rec = "LOW", 1.0, "Normal sizing."
            return {"frequency": big_days, "current_near_circuit": near,
                    "risk_level": level, "recommendation": rec, "size_factor": factor}
        except Exception:
            return default
