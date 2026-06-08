"""
Scout agent — weekly candidate discovery.

Screens a fixed watchlist of Nifty 50 large-caps. ALL scoring is deterministic code
over real price data — no LLM is involved in producing numbers (CLAUDE.md rule 4).

TODO: Add real-time news screening via SerpAPI or similar in a future iteration.
"""
from __future__ import annotations

from rich.console import Console

from src.agents.technical.indicators import compute_indicators
from src.data.fetcher import MarketDataFetcher

console = Console()

# 20 Nifty 50 large-caps. {symbol: display name}
WATCHLIST: dict[str, str] = {
    "RELIANCE": "Reliance Industries",
    "TCS": "Tata Consultancy Services",
    "HDFCBANK": "HDFC Bank",
    "INFY": "Infosys",
    "ICICIBANK": "ICICI Bank",
    "HINDUNILVR": "Hindustan Unilever",
    "ITC": "ITC",
    "SBIN": "State Bank of India",
    "BHARTIARTL": "Bharti Airtel",
    "KOTAKBANK": "Kotak Mahindra Bank",
    "LT": "Larsen & Toubro",
    "AXISBANK": "Axis Bank",
    "ASIANPAINT": "Asian Paints",
    "MARUTI": "Maruti Suzuki",
    "TITAN": "Titan Company",
    "SUNPHARMA": "Sun Pharmaceutical",
    "BAJFINANCE": "Bajaj Finance",
    "WIPRO": "Wipro",
    "NESTLEIND": "Nestle India",
    "ULTRACEMCO": "UltraTech Cement",
}


class ScoutAgent:
    def __init__(self) -> None:
        self.fetcher = MarketDataFetcher()

    def _score_symbol(self, symbol: str):
        df = self.fetcher.get_price_history(symbol, period="1y")
        if df is None or df.empty:
            return None

        ind = compute_indicators(df)
        price = float(df["Close"].iloc[-1])
        year_high = float(df["High"].max())

        score = 0
        reasons: list[str] = []

        # Within 5% of 52-week high.
        if year_high > 0 and price >= year_high * 0.95:
            score += 2
            reasons.append("within 5% of 52w high")

        # Above-average volume.
        if ind["volume_ratio"] is not None and ind["volume_ratio"] > 1.5:
            score += 1
            reasons.append(f"volume {ind['volume_ratio']}x avg")

        # Momentum RSI band (not overbought).
        if ind["rsi_14"] is not None and 50 <= ind["rsi_14"] <= 65:
            score += 2
            reasons.append(f"RSI {ind['rsi_14']} (momentum)")

        # Above both moving averages.
        if (
            ind["ma_50"] is not None
            and ind["ma_200"] is not None
            and price > ind["ma_50"]
            and price > ind["ma_200"]
        ):
            score += 2
            reasons.append("above MA50 & MA200")

        return {
            "symbol": symbol,
            "name": WATCHLIST.get(symbol, symbol),
            "score": score,
            "rationale": "; ".join(reasons) if reasons else "no strong signals",
        }

    def scan(self, sectors: list[str] | None = None) -> list[dict]:
        results: list[dict] = []
        for symbol in WATCHLIST:
            scored = self._score_symbol(symbol)
            if scored is not None:
                results.append(scored)

        results.sort(key=lambda c: c["score"], reverse=True)
        top = results[:3]
        if top:
            summary = ", ".join("{0}({1})".format(c["symbol"], c["score"]) for c in top)
            console.print(f"[cyan][SCOUT] Top candidates: {summary}[/cyan]")
        return top
