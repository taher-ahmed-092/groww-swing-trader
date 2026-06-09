"""
Market regime detection — classifies the Nifty environment and sets a position-size
multiplier. The system trades differently in a bull trend vs a volatile chop.
"""
from __future__ import annotations

from src.agents.technical.indicators import compute_indicators
from src.data.fetcher import MarketDataFetcher


class RegimeDetector:
    def __init__(self) -> None:
        self.fetcher = MarketDataFetcher()

    def detect(self) -> dict:
        # ^NSEI must bypass the .NS suffix logic — fetch directly.
        df = None
        try:
            import yfinance as yf
            raw = yf.Ticker("^NSEI").history(period="1y")
            if raw is not None and not raw.empty:
                df = raw.dropna(subset=["Close"])
        except Exception:
            df = None

        if df is None or df.empty:
            return {
                "regime": "UNKNOWN", "strategy": "Data unavailable — trade cautiously.",
                "size_multiplier": 0.75, "nifty_price": None, "ma50": None,
                "ma200": None, "rsi": None, "adx": None,
            }

        indicators = compute_indicators(df)
        current = float(df["Close"].iloc[-1])
        recent_range = float(df["Close"].iloc[-5:].max() - df["Close"].iloc[-5:].min())
        result = self.classify(indicators, current, recent_range)
        result.update({
            "nifty_price": round(current, 2),
            "ma50": round(indicators.get("ma_50") or current, 2),
            "ma200": round(indicators.get("ma_200") or current, 2),
            "rsi": round(indicators.get("rsi_14") or 50, 2),
            "adx": round(indicators.get("adx_14") or 20, 2),
        })
        return result

    @staticmethod
    def classify(indicators: dict, current: float, recent_range: float) -> dict:
        """Pure regime classification from indicator values (unit-testable)."""
        ma50 = indicators.get("ma_50") or current
        ma200 = indicators.get("ma_200") or current
        rsi = indicators.get("rsi_14") or 50
        adx = indicators.get("adx_14") or 20
        atr = indicators.get("atr_14") or (current * 0.01)

        if current > ma200 and rsi > 50 and adx > 25:
            regime, mult = "BULL_TRENDING", 1.0
            strategy = "Full position sizes. Target wider exits. Momentum is your friend."
        elif current < ma200 and rsi < 50 and adx > 25:
            regime, mult = "BEAR_TRENDING", 0.5
            strategy = "Reduce to 50% size. Skip marginal setups. Only high-confidence entries."
        elif adx < 20 and recent_range > 1.5 * atr:
            regime, mult = "VOLATILE", 0.5
            strategy = "50% size. Tighter stops. Skip if ADX below 15. Wait for clarity."
        elif adx < 20 and recent_range < 0.8 * atr:
            regime, mult = "RANGE_BOUND", 0.75
            strategy = "Range trades only. Enter near support, exit near resistance quickly."
        elif current < ma200 and current > ma50:
            regime, mult = "RECOVERY", 0.5
            strategy = "50% size. Extra patience. Wait for MA200 reclaim before full allocation."
        else:
            regime, mult = "TRANSITIONAL", 0.75
            strategy = "Reduced size. Uncertain regime. Wait for clearer direction."

        return {"regime": regime, "strategy": strategy, "size_multiplier": mult}
