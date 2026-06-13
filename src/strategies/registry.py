"""
Strategy selector — picks the best strategy for the current regime and learns,
from journalled results, which strategy wins when. This is evidence-based
selection over a curated library: the safe version of "learning algorithms".
"""
from __future__ import annotations

import pandas as pd

from src.memory.journal import TradingJournal
from src.strategies.base import Strategy
from src.strategies.breakout import BreakoutStrategy
from src.strategies.mean_reversion import MeanReversionStrategy
from src.strategies.momentum import MomentumStrategy
from src.strategies.pairs_trading import PairsTradingStrategy

_DEFAULT_WR = 0.5  # neutral prior for strategies with no history (don't penalize new)


class StrategyRegistry:
    def __init__(self, journal: TradingJournal | None = None) -> None:
        self.journal = journal or TradingJournal()
        self.strategies: list[Strategy] = [
            MomentumStrategy(),
            MeanReversionStrategy(),
            BreakoutStrategy(),
            PairsTradingStrategy(),
        ]

    def _rank_by_performance(self, strategies: list[Strategy], regime: str) -> list[Strategy]:
        ranked = []
        for strat in strategies:
            wr = self.journal.get_strategy_win_rate(strat.name, regime)
            ranked.append((wr if wr is not None else _DEFAULT_WR, strat))
        return [s for _, s in sorted(ranked, key=lambda x: -x[0])]

    def get_strategies_for_regime(self, regime: str) -> list[Strategy]:
        suitable = [s for s in self.strategies if s.suits_regime(regime)]
        return self._rank_by_performance(suitable, regime)

    def best_signal(self, symbol: str, df: pd.DataFrame, indicators: dict,
                    context: dict, regime: str) -> dict:
        ctx = {**(context or {}), "symbol": symbol}
        for strat in self.get_strategies_for_regime(regime):
            signal = strat.generate_signal(df, indicators, ctx)
            if signal.get("signal") == "BUY":
                signal["strategy_name"] = strat.name
                return signal
        return {
            "signal": "SKIP", "score": 0.0, "strategy_name": "none",
            "entry_price": round(float(df["Close"].iloc[-1]), 2) if df is not None and not df.empty else 0.0,
            "stop_price": 0.0, "target_price": 0.0,
            "rationale": f"No strategy found a setup in {regime} regime",
        }
