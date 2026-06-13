"""Momentum — buy strength in trending markets and ride the trend."""
from __future__ import annotations

import pandas as pd

from config.risk_limits import LIMITS
from src.strategies.base import Strategy


class MomentumStrategy(Strategy):
    name = "momentum"
    description = "Buy strength in trending markets — ride the trend"
    best_regimes = ["BULL_TRENDING"]

    def generate_signal(self, df: pd.DataFrame, indicators: dict, context: dict) -> dict:
        if df is None or df.empty:
            return self._skip("no price data", self.name)
        entry = round(float(df["Close"].iloc[-1]), 2)

        rsi = indicators.get("rsi_14")
        adx = indicators.get("adx_14")
        ma50 = indicators.get("ma_50")
        ma200 = indicators.get("ma_200")
        trend = indicators.get("trend")
        atr = indicators.get("atr_14")

        # Requirements: confirmed uptrend, momentum RSI band, real trend (ADX), stack.
        if trend != "UPTREND":
            return self._skip("not an uptrend", self.name, entry)
        if rsi is None or not (50 <= rsi <= 65):
            return self._skip(f"RSI {rsi} outside 50-65 momentum band", self.name, entry)
        if adx is None or adx <= 25:
            return self._skip(f"ADX {adx} <= 25 (no strong trend)", self.name, entry)
        if not (ma50 and ma200 and entry > ma50 > ma200):
            return self._skip("price not stacked above MA50>MA200", self.name, entry)

        # ATR stop (capped at the 7% rule); trends run further → 2.5× target.
        if atr and atr > 0:
            stop = round(max(entry - 2 * atr, entry * (1 - LIMITS.stop_loss_pct / 100)), 2)
        else:
            stop = round(entry * (1 - LIMITS.stop_loss_pct / 100), 2)
        target = round(entry + 2.5 * (entry - stop), 2)

        # Score weighted by trend strength, volume, OBV.
        score = 0.5
        if adx > 30:
            score += 0.2
        if (indicators.get("volume_ratio") or 0) > 1.3:
            score += 0.15
        if indicators.get("obv_trend") == "RISING":
            score += 0.15
        score = round(min(1.0, score), 4)

        return {
            "signal": "BUY", "score": score, "entry_price": entry,
            "stop_price": stop, "target_price": target,
            "rationale": f"Momentum: uptrend, RSI {rsi}, ADX {adx}, stacked MAs",
            "strategy_name": self.name,
        }
