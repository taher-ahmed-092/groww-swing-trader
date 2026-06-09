"""
Market data access via yfinance. No API keys required.

NSE symbols get a ".NS" suffix automatically (e.g. RELIANCE -> RELIANCE.NS).
All methods are defensive: they log a warning and return None on failure, never raise.
"""
from __future__ import annotations

import pandas as pd
import yfinance as yf
from rich.console import Console

console = Console()


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
                console.print(f"[yellow]No price history for {symbol}[/yellow]")
                return None
            df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
            # Drop trailing/incomplete candles (yfinance can return a NaN Close for
            # the current in-progress session).
            df = df.dropna(subset=["Close"])
            if df.empty:
                return None
            return df
        except Exception as exc:
            console.print(f"[yellow]get_price_history failed for {symbol}: {exc}[/yellow]")
            return None

    def get_fundamentals(self, symbol: str) -> dict | None:
        try:
            ticker = yf.Ticker(self._to_yf_symbol(symbol))
            info = ticker.info
            if not info:
                console.print(f"[yellow]No fundamentals for {symbol}[/yellow]")
                return None
            return dict(info)
        except Exception as exc:
            console.print(f"[yellow]get_fundamentals failed for {symbol}: {exc}[/yellow]")
            return None

    def get_current_price(self, symbol: str) -> float | None:
        try:
            df = self.get_price_history(symbol, period="5d")
            if df is None or df.empty:
                return None
            return float(df["Close"].iloc[-1])
        except Exception as exc:
            console.print(f"[yellow]get_current_price failed for {symbol}: {exc}[/yellow]")
            return None
