"""
Base class for trading strategies.

A strategy is a self-contained set of deterministic rules that produces a signal.
The system maintains a LIBRARY of these and selects the best one for the current
market regime, learning from results which works when. These are well-known,
battle-tested setups — not downloaded code. Pure code, no LLM (CLAUDE.md rule 4).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    name: str = "base"
    description: str = ""
    best_regimes: list[str] = []  # which regimes this strategy suits

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame, indicators: dict, context: dict) -> dict:
        """Return a signal dict:
        {signal: BUY|HOLD|SKIP, score: 0-1, entry_price, stop_price, target_price,
         rationale, strategy_name}
        """

    def suits_regime(self, regime: str) -> bool:
        return regime in self.best_regimes or "ALL" in self.best_regimes

    @staticmethod
    def _skip(reason: str, name: str, entry: float = 0.0) -> dict:
        return {
            "signal": "SKIP", "score": 0.0, "entry_price": entry,
            "stop_price": 0.0, "target_price": 0.0,
            "rationale": reason, "strategy_name": name,
        }
