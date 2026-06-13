"""Pairs trading — divergence detection (fetcher stubbed for determinism)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.strategies.pairs_trading import PairsTradingStrategy


def _series(values):
    return pd.DataFrame({"Open": values, "High": [v + 1 for v in values],
                         "Low": [v - 1 for v in values], "Close": values,
                         "Volume": [100000] * len(values)})


def _patch_fetcher(strat, mapping):
    def fake(symbol, period="6mo", interval="1d"):
        return mapping.get(symbol)
    strat.fetcher.get_price_history = fake


def test_pairs_finds_divergence(monkeypatch):
    strat = PairsTradingStrategy()
    n = 70
    a = [100 + i * 0.5 for i in range(n)]
    b = [50 + i * 0.25 for i in range(n)]
    a[-1] += 18  # leader A spikes → ratio diverges → buy laggard B
    _patch_fetcher(strat, {"HDFCBANK": _series(a), "ICICIBANK": _series(b)})
    opps = strat.find_opportunities()
    assert opps and opps[0]["buy_symbol"] == "ICICIBANK"
    assert abs(opps[0]["z_score"]) >= 2.0


def test_pairs_requires_correlation(monkeypatch):
    strat = PairsTradingStrategy()
    rng = np.random.RandomState(3)
    a = list(100 + rng.randn(70) * 5)
    b = list(100 + rng.randn(70) * 5)  # independent → low correlation
    _patch_fetcher(strat, {"HDFCBANK": _series(a), "ICICIBANK": _series(b)})
    assert strat.find_opportunities() == []


def test_pairs_z_score_threshold(monkeypatch):
    strat = PairsTradingStrategy()
    n = 70
    a = [100 + i * 0.5 for i in range(n)]
    b = [50 + i * 0.25 for i in range(n)]  # constant ratio, no divergence → z≈0
    _patch_fetcher(strat, {"HDFCBANK": _series(a), "ICICIBANK": _series(b)})
    assert strat.find_opportunities() == []
