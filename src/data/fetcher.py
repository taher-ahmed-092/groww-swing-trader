"""
Market data access via yfinance. No API keys required.

NSE symbols get a ".NS" suffix automatically (e.g. RELIANCE -> RELIANCE.NS).
All methods are defensive: a missing/bad symbol is a routine DEBUG-level skip, not
an error — we never raise and never spam the console with yfinance 404 tracebacks.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque

import pandas as pd
import yfinance as yf

# Silence yfinance's own chatter ("$X: possibly delisted", HTTP 404 traces).
for _noisy in ("yfinance", "yfinance.data", "yfinance.utils", "yfinance.ticker", "peewee"):
    logging.getLogger(_noisy).setLevel(logging.CRITICAL)

log = logging.getLogger(__name__)


class _RateLimiter:
    """Shared token-bucket across scout/replay/cont-sim/pairs — all of them
    call through MarketDataFetcher, so gating here caps the COMBINED yfinance
    call rate process-wide rather than each caller independently rate-limiting
    itself (which wouldn't prevent their aggregate from tripping throttling).
    On exhaustion, sleeps rather than failing the caller."""

    def __init__(self, max_calls_per_minute: int) -> None:
        self.max_calls = max_calls_per_minute
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.time()
                while self._calls and now - self._calls[0] > 60:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                sleep_for = max(0.05, 60 - (now - self._calls[0]) + 0.05)
            time.sleep(sleep_for)


_yfinance_rate_limiter = _RateLimiter(int(os.environ.get("YFINANCE_MAX_CALLS_PER_MIN", "60")))

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
        _yfinance_rate_limiter.acquire()
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
