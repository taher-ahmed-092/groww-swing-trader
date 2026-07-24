"""
Pairs trading — long-only statistical arbitrage.

Market-NEUTRAL: when two historically correlated stocks diverge unusually, buy the
laggard expecting it to catch up. This is the answer to "no trades in a downtrend" —
it has candidates in any regime. Pure code, no shorting, no paid data.
"""
from __future__ import annotations

import pandas as pd

from config.risk_limits import LIMITS
from src.data.fetcher import MarketDataFetcher
from src.strategies.base import Strategy
from src.utils.serialization import sanitize_for_state

# Same-sector, fundamentally linked, historically correlated pairs.
STOCK_PAIRS = [
    ("HDFCBANK", "ICICIBANK", "Banking"),
    ("TCS", "INFY", "IT"),
    ("RELIANCE", "ONGC", "Energy"),
    ("MARUTI", "EICHERMOT", "Auto"),
    ("SUNPHARMA", "DRREDDY", "Pharma"),
    ("HINDUNILVR", "ITC", "FMCG"),
    ("LT", "ADANIPORTS", "Infra"),
    ("AXISBANK", "KOTAKBANK", "Banking"),
]

_Z_ENTRY = 2.0
_CORR_MIN = 0.7


class PairsTradingStrategy(Strategy):
    name = "pairs_trading"
    description = "Market-neutral: buy the laggard when a correlated pair diverges"
    best_regimes = ["ALL"]

    def __init__(self) -> None:
        self.fetcher = MarketDataFetcher()

    def find_opportunities(self) -> list[dict]:
        opportunities: list[dict] = []
        for sym_a, sym_b, sector in STOCK_PAIRS:
            df_a = self.fetcher.get_price_history(sym_a, period="6mo")
            df_b = self.fetcher.get_price_history(sym_b, period="6mo")
            if df_a is None or df_b is None:
                continue
            merged = pd.DataFrame({"a": df_a["Close"], "b": df_b["Close"]}).dropna()
            if len(merged) < 60:
                continue

            correlation = merged["a"].corr(merged["b"])
            if correlation is None or correlation < _CORR_MIN:
                continue

            ratio = merged["a"] / merged["b"]
            window = ratio.tail(60)
            mean_ratio = float(window.mean())
            std_ratio = float(window.std())
            if std_ratio == 0:
                continue
            z = (float(ratio.iloc[-1]) - mean_ratio) / std_ratio
            if abs(z) < _Z_ENTRY:
                continue

            # Buy the laggard (the relatively cheap one).
            if z > _Z_ENTRY:
                buy_sym, lead_sym = sym_b, sym_a
                buy_price = float(merged["b"].iloc[-1])
            else:
                buy_sym, lead_sym = sym_a, sym_b
                buy_price = float(merged["a"].iloc[-1])

            expected_move_pct = abs(z) * 0.6 * (std_ratio / mean_ratio)
            target = round(buy_price * (1 + min(expected_move_pct, 0.10)), 2)
            stop = round(buy_price * (1 - LIMITS.stop_loss_pct / 100), 2)

            opportunities.append({
                "buy_symbol": buy_sym, "pair_symbol": lead_sym,
                "z_score": round(z, 2), "correlation": round(correlation, 3),
                "entry_price": round(buy_price, 2), "stop_price": stop,
                "target_price": target,
                "rationale": (f"{buy_sym} lagging {lead_sym} by {abs(z):.1f}σ "
                              f"(corr {correlation:.2f}) — mean reversion expected"),
                "sector": sector,
            })
        opportunities.sort(key=lambda x: abs(x["z_score"]), reverse=True)
        return [sanitize_for_state(opp) for opp in opportunities]

    def generate_signal(self, df: pd.DataFrame, indicators: dict, context: dict) -> dict:
        """Fires when a precomputed pairs opportunity for this symbol is in context."""
        opp = (context or {}).get("pairs_opportunity")
        symbol = (context or {}).get("symbol")
        if not opp or (symbol and opp.get("buy_symbol") != symbol):
            return self._skip("no pairs divergence for this symbol", self.name)
        # z 2.0→0.5, 3.0→0.8 (cap 0.9).
        score = round(min(0.9, 0.5 + (abs(opp["z_score"]) - 2.0) * 0.3), 4)
        return {
            "signal": "BUY", "score": score,
            "entry_price": opp["entry_price"], "stop_price": opp["stop_price"],
            "target_price": opp["target_price"], "rationale": opp["rationale"],
            "strategy_name": self.name,
        }
