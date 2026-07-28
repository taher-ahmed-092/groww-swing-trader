"""
Market data access via yfinance. No API keys required.

NSE symbols get a ".NS" suffix automatically (e.g. RELIANCE -> RELIANCE.NS).
All methods are defensive: a missing/bad symbol is a routine DEBUG-level skip, not
an error — we never raise and never spam the console with yfinance 404 tracebacks.
"""
from __future__ import annotations

import logging
import time

import pandas as pd
import yfinance as yf

# Silence yfinance's own chatter ("$X: possibly delisted", HTTP 404 traces).
for _noisy in ("yfinance", "yfinance.data", "yfinance.utils", "yfinance.ticker", "peewee"):
    logging.getLogger(_noisy).setLevel(logging.CRITICAL)

log = logging.getLogger(__name__)

# Module-level (process-wide) in-memory cache, keyed by (symbol, period, interval),
# with a 15-minute TTL. Root cause of the --scan timeout: scout, technical, and
# pairs each independently re-fetch the same symbol's price history within a
# single run, and each fetch is a real network round-trip — across 241 watchlist
# symbols that redundancy alone can add minutes. A short TTL is safe: intraday
# price data doesn't meaningfully change inside 15 minutes for swing-trade sizing.
_PRICE_HISTORY_TTL_SECONDS = 15 * 60
_price_history_cache: dict[tuple[str, str, str], tuple[float, pd.DataFrame | None]] = {}


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
        cache_key = (symbol.strip().upper(), period, interval)
        cached = _price_history_cache.get(cache_key)
        if cached is not None and (time.time() - cached[0]) < _PRICE_HISTORY_TTL_SECONDS:
            return cached[1]

        df = self._fetch_price_history(symbol, period, interval)
        _price_history_cache[cache_key] = (time.time(), df)
        return df

    def _fetch_price_history(
        self, symbol: str, period: str, interval: str
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
            if not self._is_valid(df):
                log.debug("Skipping %s: failed data validation", symbol)
                return None
            return df
        except Exception as exc:  # any failure → quiet skip, never raise
            log.debug("Skipping %s: %s", symbol, exc)
            return None

    @staticmethod
    def _is_valid(df: pd.DataFrame) -> bool:
        """Reject obviously corrupt data: NaN closes, absurd jumps, dead volume."""
        try:
            tail = df.tail(5)
            if tail["Close"].isna().any():
                return False
            moves = df["Close"].pct_change().abs().dropna()
            if not moves.empty and moves.max() > 0.25:  # single-day >25% = bad data
                return False
            if (tail["Volume"].fillna(0) <= 0).all():  # all-zero volume = stale/bad
                return False
            return True
        except Exception:
            return True  # validation must never block on its own error

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
