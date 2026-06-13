"""Mean reversion — buy oversold bounces in range-bound / choppy markets."""
from __future__ import annotations

import pandas as pd

from src.strategies.base import Strategy


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"
    description = "Buy oversold bounces in range-bound/choppy markets"
    best_regimes = ["RANGE_BOUND", "VOLATILE"]

    def generate_signal(self, df: pd.DataFrame, indicators: dict, context: dict) -> dict:
        if df is None or df.empty:
            return self._skip("no price data", self.name)
        entry = round(float(df["Close"].iloc[-1]), 2)

        rsi = indicators.get("rsi_14")
        ma200 = indicators.get("ma_200")
        ma50 = indicators.get("ma_50")
        atr = indicators.get("atr_14") or (entry * 0.02)
        support = indicators.get("support")
        bb_lower = indicators.get("bb_lower")
        s1 = indicators.get("s1")

        # NEVER catch a falling knife: require price still above the 200-day mean.
        if ma200 is not None and entry < ma200:
            return self._skip("below MA200 — would be a falling knife", self.name, entry)
        # Confirmed downtrend regimes are excluded at the registry level too.
        if rsi is None or rsi >= 40:
            return self._skip(f"RSI {rsi} not oversold (<40)", self.name, entry)

        # Near a support level (within 1×ATR of 20d low / Bollinger lower / S1).
        supports = [s for s in (support, bb_lower, s1) if s]
        near = any(abs(entry - s) <= atr for s in supports)
        if not near:
            return self._skip("not near a support level", self.name, entry)

        stop = round(min(supports) - atr, 2) if supports else round(entry - 2 * atr, 2)
        if stop <= 0 or stop >= entry:
            stop = round(entry - 2 * atr, 2)
        # Quick reversion target: the 20-day mean (Bollinger middle / MA20≈MA50 proxy).
        target = round(indicators.get("bb_mid") or ma50 or (entry * 1.04), 2)
        if target <= entry:
            target = round(entry * 1.04, 2)

        score = round(min(1.0, 0.5 + (40 - rsi) / 40 * 0.5), 4)  # more oversold → higher
        return {
            "signal": "BUY", "score": score, "entry_price": entry,
            "stop_price": stop, "target_price": target,
            "rationale": f"Mean reversion: RSI {rsi} oversold near support, reverting to mean",
            "strategy_name": self.name,
        }
