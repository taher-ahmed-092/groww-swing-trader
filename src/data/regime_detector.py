"""
Market regime detection — classifies the Nifty environment and sets a position-size
multiplier. The system trades differently in a bull trend vs a volatile chop.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.agents.technical.indicators import compute_indicators
from src.data.fetcher import MarketDataFetcher

IST = ZoneInfo("Asia/Kolkata")
LAST_KNOWN_REGIME_FILE = Path("data/cache/last_known_regime.json")


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
            return self._load_last_known()

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
            "stale": False,
        })
        self._save_last_known(result)
        return result

    @staticmethod
    def _save_last_known(result: dict) -> None:
        try:
            LAST_KNOWN_REGIME_FILE.parent.mkdir(parents=True, exist_ok=True)
            cached = dict(result)
            cached["cached_at"] = datetime.now(IST).strftime("%d %b %H:%M")
            LAST_KNOWN_REGIME_FILE.write_text(json.dumps(cached))
        except Exception:
            pass  # caching the regime must never block a live detection

    @staticmethod
    def _load_last_known() -> dict:
        try:
            if LAST_KNOWN_REGIME_FILE.exists():
                cached = json.loads(LAST_KNOWN_REGIME_FILE.read_text())
                cached["stale"] = True
                return cached
        except Exception:
            pass

        # No cache ever written (e.g. first-ever run with NSE closed) — the
        # only case where UNKNOWN/₹0 is unavoidable.
        return {
            "regime": "UNKNOWN", "strategy": "Data unavailable — trade cautiously.",
            "size_multiplier": 0.75, "nifty_price": None, "ma50": None,
            "ma200": None, "rsi": None, "adx": None, "stale": True, "cached_at": None,
        }

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
