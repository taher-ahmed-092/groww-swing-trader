"""
Scout agent — weekly candidate discovery.

Screens a sectored watchlist of ~50 Nifty / Nifty Next 50 names. ALL scoring is
deterministic code over real price data (CLAUDE.md rule 4) — the LLM is never
involved in producing numbers. Each stock is scored 0-10 across five dimensions,
filtered for earnings proximity, and the top 5 are returned.

TODO: Add real-time news screening via SerpAPI or similar in a future iteration.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import yfinance as yf
from rich.console import Console

from src.agents.technical.indicators import compute_indicators
from src.data.fetcher import MarketDataFetcher
from src.memory.lessons import LessonsRetriever

console = Console()

# ~50 Nifty / Nifty Next 50 names, ~6 per sector. {symbol: {name, sector}}
WATCHLIST: dict[str, dict[str, str]] = {
    # Banking
    "HDFCBANK": {"name": "HDFC Bank", "sector": "Banking"},
    "ICICIBANK": {"name": "ICICI Bank", "sector": "Banking"},
    "SBIN": {"name": "State Bank of India", "sector": "Banking"},
    "KOTAKBANK": {"name": "Kotak Mahindra Bank", "sector": "Banking"},
    "AXISBANK": {"name": "Axis Bank", "sector": "Banking"},
    "INDUSINDBK": {"name": "IndusInd Bank", "sector": "Banking"},
    # IT
    "TCS": {"name": "Tata Consultancy Services", "sector": "IT"},
    "INFY": {"name": "Infosys", "sector": "IT"},
    "WIPRO": {"name": "Wipro", "sector": "IT"},
    "HCLTECH": {"name": "HCL Technologies", "sector": "IT"},
    "TECHM": {"name": "Tech Mahindra", "sector": "IT"},
    "LTIM": {"name": "LTIMindtree", "sector": "IT"},
    # FMCG
    "HINDUNILVR": {"name": "Hindustan Unilever", "sector": "FMCG"},
    "ITC": {"name": "ITC", "sector": "FMCG"},
    "NESTLEIND": {"name": "Nestle India", "sector": "FMCG"},
    "BRITANNIA": {"name": "Britannia Industries", "sector": "FMCG"},
    "DABUR": {"name": "Dabur India", "sector": "FMCG"},
    "TATACONSUM": {"name": "Tata Consumer Products", "sector": "FMCG"},
    # Auto
    "MARUTI": {"name": "Maruti Suzuki", "sector": "Auto"},
    "TATAMOTORS": {"name": "Tata Motors", "sector": "Auto"},
    "M&M": {"name": "Mahindra & Mahindra", "sector": "Auto"},
    "BAJAJ-AUTO": {"name": "Bajaj Auto", "sector": "Auto"},
    "EICHERMOT": {"name": "Eicher Motors", "sector": "Auto"},
    "HEROMOTOCO": {"name": "Hero MotoCorp", "sector": "Auto"},
    # Pharma
    "SUNPHARMA": {"name": "Sun Pharmaceutical", "sector": "Pharma"},
    "DRREDDY": {"name": "Dr Reddy's Labs", "sector": "Pharma"},
    "CIPLA": {"name": "Cipla", "sector": "Pharma"},
    "DIVISLAB": {"name": "Divi's Laboratories", "sector": "Pharma"},
    "APOLLOHOSP": {"name": "Apollo Hospitals", "sector": "Pharma"},
    "AUROPHARMA": {"name": "Aurobindo Pharma", "sector": "Pharma"},
    # Energy
    "RELIANCE": {"name": "Reliance Industries", "sector": "Energy"},
    "ONGC": {"name": "Oil & Natural Gas Corp", "sector": "Energy"},
    "NTPC": {"name": "NTPC", "sector": "Energy"},
    "POWERGRID": {"name": "Power Grid Corp", "sector": "Energy"},
    "COALINDIA": {"name": "Coal India", "sector": "Energy"},
    "BPCL": {"name": "Bharat Petroleum", "sector": "Energy"},
    # Metals
    "TATASTEEL": {"name": "Tata Steel", "sector": "Metals"},
    "JSWSTEEL": {"name": "JSW Steel", "sector": "Metals"},
    "HINDALCO": {"name": "Hindalco Industries", "sector": "Metals"},
    "VEDL": {"name": "Vedanta", "sector": "Metals"},
    "JINDALSTEL": {"name": "Jindal Steel & Power", "sector": "Metals"},
    "NMDC": {"name": "NMDC", "sector": "Metals"},
    # Infra
    "LT": {"name": "Larsen & Toubro", "sector": "Infra"},
    "ADANIPORTS": {"name": "Adani Ports & SEZ", "sector": "Infra"},
    "ULTRACEMCO": {"name": "UltraTech Cement", "sector": "Infra"},
    "GRASIM": {"name": "Grasim Industries", "sector": "Infra"},
    "SHREECEM": {"name": "Shree Cement", "sector": "Infra"},
    "DLF": {"name": "DLF", "sector": "Infra"},
}


class ScoutAgent:
    def __init__(self) -> None:
        self.fetcher = MarketDataFetcher()
        self.lessons = LessonsRetriever()

    # ── helpers ──────────────────────────────────────────────────────────────
    def _nifty_1m_return(self) -> float | None:
        try:
            df = yf.Ticker("^NSEI").history(period="2mo")
            if df is None or len(df) < 21:
                return None
            return (float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-21]) - 1) * 100
        except Exception:
            return None

    def _days_to_earnings(self, symbol: str) -> int | None:
        """Days until next earnings via yfinance calendar. None if unknown."""
        try:
            cal = yf.Ticker(f"{symbol}.NS").calendar
            dates = None
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date")
            elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
                dates = cal.loc["Earnings Date"].tolist()
            if not dates:
                return None
            next_date = dates[0] if isinstance(dates, (list, tuple)) else dates
            next_dt = pd.Timestamp(next_date).to_pydatetime()
            if next_dt.tzinfo is None:
                next_dt = next_dt.replace(tzinfo=timezone.utc)
            delta = (next_dt - datetime.now(timezone.utc)).days
            return delta if delta >= 0 else None
        except Exception:
            return None

    def _score_symbol(self, symbol: str, nifty_1m: float | None) -> dict | None:
        df = self.fetcher.get_price_history(symbol, period="1y")
        if df is None or df.empty:
            return None

        ind = compute_indicators(df)
        meta = WATCHLIST.get(symbol, {"name": symbol, "sector": "Unknown"})
        price = float(df["Close"].iloc[-1])
        year_high = float(df["High"].max())
        year_low = float(df["Low"].min())

        score = 0
        reasons: list[str] = []
        flags: list[str] = []

        # 1. Momentum (0-3).
        rsi = ind["rsi_14"]
        if rsi is not None:
            if 50 <= rsi <= 65:
                score += 2
                reasons.append(f"RSI {rsi} momentum zone")
            elif 40 <= rsi < 50:
                score += 1
                reasons.append(f"RSI {rsi} building")
            elif rsi > 70 or rsi < 30:
                flags.append("rsi_extreme")

        # 2. Trend (0-3).
        ma50, ma200 = ind["ma_50"], ind["ma_200"]
        if ma50 is not None and ma200 is not None:
            if price > ma50 > ma200:
                score += 3
                reasons.append("price>MA50>MA200 strong uptrend")
            elif price > ma50 and ma50 < ma200:
                score += 1
                reasons.append("recovering above MA50")
        elif ma50 is not None and price > ma50:
            score += 1
            reasons.append("above MA50")

        # 3. Volume (0-2).
        vr = ind["volume_ratio"]
        if vr is not None:
            if vr > 2.0:
                score += 2
                reasons.append(f"volume {vr}x (strong)")
            elif vr >= 1.5:
                score += 1
                reasons.append(f"volume {vr}x")

        # 4. Relative strength (0-1).
        if len(df) >= 21 and nifty_1m is not None:
            stock_1m = (price / float(df["Close"].iloc[-21]) - 1) * 100
            if stock_1m > nifty_1m:
                score += 1
                reasons.append(f"outperforming Nifty 1m ({stock_1m:.1f}% vs {nifty_1m:.1f}%)")

        # 5. Position in 52W range (0-1).
        if year_high > year_low:
            pos = (price - year_low) / (year_high - year_low)
            if 0.6 <= pos <= 0.9:
                score += 1
                reasons.append(f"{pos * 100:.0f}% of 52W range (near highs, not extended)")

        return {
            "symbol": symbol,
            "name": meta["name"],
            "sector": meta["sector"],
            "score": score,
            "rationale": "; ".join(reasons) if reasons else "no strong signals",
            "flags": flags,
        }

    # ── public API ─────────────────────────────────────────────────────────────
    def scan(self, sectors: list[str] | None = None) -> list[dict]:
        # Historical losing-setup context (advisory; empty when journal is fresh).
        try:
            failure_patterns = self.lessons.get_failure_patterns()
        except Exception:
            failure_patterns = []

        nifty_1m = self._nifty_1m_return()

        symbols = [
            s for s in WATCHLIST
            if not sectors or WATCHLIST[s]["sector"] in sectors
        ]

        scored: list[dict] = []
        for symbol in symbols:
            candidate = self._score_symbol(symbol, nifty_1m)
            if candidate is not None:
                scored.append(candidate)

        scored.sort(key=lambda c: c["score"], reverse=True)

        # Earnings filter only on the strongest names (limits slow calendar calls).
        for candidate in scored[:8]:
            days = self._days_to_earnings(candidate["symbol"])
            if days is not None and days < 7:
                candidate["score"] = 0
                candidate["flags"].append("earnings_risk")
                candidate["rationale"] += f"; earnings in {days}d (earnings_risk)"

        # Advisory: surface known failure patterns so the operator avoids repeats.
        if failure_patterns:
            for candidate in scored:
                candidate["rationale"] += " | watch past failures: " + "; ".join(failure_patterns)

        scored.sort(key=lambda c: c["score"], reverse=True)
        top = scored[:5]
        if top:
            summary = ", ".join("{0}({1})".format(c["symbol"], c["score"]) for c in top)
            console.print(f"[cyan][SCOUT] Top candidates: {summary}[/cyan]")
        return top
