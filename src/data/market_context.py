"""
Market regime context. No API keys (yfinance).

Tells the rest of the system whether the broad market (Nifty 50) is in a posture
where buying makes sense. "Don't fight the index": during a Nifty downtrend, buy
signals get flagged and the judge penalizes them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yfinance as yf
from rich.console import Console
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator

console = Console()

# Sector → NSE index/ETF proxy symbol (yfinance tickers).
_SECTOR_ETF: dict[str, str] = {
    "IT": "^CNXIT",
    "Banking": "^NSEBANK",
    "FMCG": "^CNXFMCG",
    "Auto": "^CNXAUTO",
    "Pharma": "^CNXPHARMA",
    "Energy": "^CNXENERGY",
    "Metals": "^CNXMETAL",
    "Infra": "^CNXINFRA",
}


def _safe_history(symbol: str, period: str = "300d") -> pd.DataFrame | None:
    try:
        df = yf.Ticker(symbol).history(period=period)
        if df is None or df.empty:
            return None
        return df.dropna(subset=["Close"])
    except Exception as exc:
        console.print(f"[yellow]market data fetch failed for {symbol}: {exc}[/yellow]")
        return None


class MarketContext:
    def get_nifty_context(self) -> dict:
        default = {
            "nifty_trend": "SIDEWAYS",
            "nifty_rsi": None,
            "nifty_above_ma200": False,
            "market_safe_to_buy": True,  # unknown regime → allow; judge still scrutinizes
            "context_summary": "Nifty data unavailable — proceeding with caution",
        }

        df = _safe_history("^NSEI", period="300d")
        if df is None or len(df) < 50:
            return default

        close = df["Close"]
        price = float(close.iloc[-1])

        ma50_series = SMAIndicator(close, window=50).sma_indicator()
        ma200_series = SMAIndicator(close, window=200).sma_indicator() if len(df) >= 200 else None
        ma50 = float(ma50_series.iloc[-1]) if not pd.isna(ma50_series.iloc[-1]) else None
        ma200 = (
            float(ma200_series.iloc[-1])
            if ma200_series is not None and not pd.isna(ma200_series.iloc[-1])
            else None
        )

        rsi_series = RSIIndicator(close, window=14).rsi()
        rsi = round(float(rsi_series.iloc[-1]), 2) if not pd.isna(rsi_series.iloc[-1]) else None

        # MA50 slope over the last 5 values.
        ma50_slope = 0.0
        tail = ma50_series.dropna().tail(5)
        if len(tail) >= 2:
            ma50_slope = float(np.polyfit(range(len(tail)), tail.values, 1)[0])

        # Classify trend.
        if ma50 is not None and ma200 is not None:
            if price > ma50 > ma200 and ma50_slope > 0:
                trend = "UPTREND"
            elif price < ma50 < ma200:
                trend = "DOWNTREND"
            else:
                trend = "SIDEWAYS"
        elif ma50 is not None:
            trend = "UPTREND" if price > ma50 and ma50_slope > 0 else "SIDEWAYS"
        else:
            trend = "SIDEWAYS"

        above_ma200 = bool(ma200 is not None and price > ma200)
        safe = trend != "DOWNTREND"

        context = {
            "nifty_trend": trend,
            "nifty_rsi": rsi,
            "nifty_above_ma200": above_ma200,
            "market_safe_to_buy": safe,
            "context_summary": (
                f"Nifty {trend}, RSI {rsi}, "
                f"{'safe to buy' if safe else 'AVOID buys (downtrend)'}"
            ),
        }

        # Institutional flow + economic calendar — real-time edge beyond EOD prices.
        try:
            from src.data.realtime_feeds import EconomicCalendar, FIIDIIFeed

            fiidii = FIIDIIFeed().get_latest()
            context["fii_dii_signal"] = fiidii.get("signal", "NEUTRAL")
            context["fii_dii_reason"] = fiidii.get("signal_reason", "")
            context["fii_net_crore"] = fiidii.get("fii_net_crore", 0)

            should_reduce, reason = EconomicCalendar().should_reduce_size()
            if should_reduce:
                context["economic_event_flag"] = reason
        except Exception:
            pass

        return context

    def get_sector_performance(self, sector: str) -> dict:
        none_dict = {
            "sector_trend": None,
            "sector_return_1m": None,
            "outperforming_nifty": None,
        }
        etf = _SECTOR_ETF.get(sector)
        if etf is None:
            return none_dict

        sec_df = _safe_history(etf, period="300d")
        nifty_df = _safe_history("^NSEI", period="60d")
        if sec_df is None or len(sec_df) < 50:
            return none_dict

        close = sec_df["Close"]
        price = float(close.iloc[-1])
        ma50 = float(SMAIndicator(close, window=50).sma_indicator().iloc[-1])

        def _ret_1m(df: pd.DataFrame | None) -> float | None:
            if df is None or len(df) < 21:
                return None
            return round((float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-21]) - 1) * 100, 4)

        sector_return_1m = _ret_1m(sec_df)
        nifty_return_1m = _ret_1m(nifty_df)

        if price > ma50:
            sector_trend = "UPTREND"
        elif price < ma50:
            sector_trend = "DOWNTREND"
        else:
            sector_trend = "SIDEWAYS"

        outperforming = (
            sector_return_1m is not None
            and nifty_return_1m is not None
            and sector_return_1m > nifty_return_1m
        )

        return {
            "sector_trend": sector_trend,
            "sector_return_1m": sector_return_1m,
            "outperforming_nifty": bool(outperforming),
        }
