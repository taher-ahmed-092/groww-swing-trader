"""
Market data access via yfinance. No API keys required.

NSE symbols get a ".NS" suffix automatically (e.g. RELIANCE -> RELIANCE.NS).
All methods are defensive: a missing/bad symbol is a routine DEBUG-level skip, not
an error — we never raise and never spam the console with yfinance 404 tracebacks.
"""
from __future__ import annotations

import logging

import pandas as pd
import yfinance as yf

# Silence yfinance's own chatter ("$X: possibly delisted", HTTP 404 traces).
for _noisy in ("yfinance", "yfinance.data", "yfinance.utils", "yfinance.ticker", "peewee"):
    logging.getLogger(_noisy).setLevel(logging.CRITICAL)

log = logging.getLogger(__name__)


class MarketDataFetcher:
    @staticmethod
    def _to_yf_symbol(symbol: str) -> str:
        symbol = symbol.strip().upper()
        if symbol.endswith(".NS") or symbol.endswith(".BO"):
            return symbol
        return f"{symbol}.NS"

    def get_price_history(
        self, symbol: str, period: str = "6mo", interval: str = "1d"
    ) -> pd.DataFrame | None:
        try:
            ticker = yf.Ticker(self._to_yf_symbol(symbol))
            df = ticker.history(period=period, interval=interval)
            if df is None or df.empty:
                log.debug("Skipping %s: data unavailable", symbol)
                return None
            df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
            # Drop trailing/incomplete candles (yfinance can return a NaN Close for
            # the current in-progress session).
            df = df.dropna(subset=["Close"])
            if df.empty:
                return None
            return df
        except Exception as exc:  # any failure → quiet skip, never raise
            log.debug("Skipping %s: %s", symbol, exc)
            return None

    def get_fundamentals(self, symbol: str) -> dict | None:
        try:
            ticker = yf.Ticker(self._to_yf_symbol(symbol))
            info = ticker.info
            if not info:
                log.debug("No fundamentals for %s", symbol)
                return None
            return dict(info)
        except Exception as exc:
            log.debug("Fundamentals unavailable for %s: %s", symbol, exc)
            return None

    def get_current_price(self, symbol: str) -> float | None:
        try:
            df = self.get_price_history(symbol, period="5d")
            if df is None or df.empty:
                return None
            return float(df["Close"].iloc[-1])
        except Exception as exc:
            log.debug("Current price unavailable for %s: %s", symbol, exc)
            return None
