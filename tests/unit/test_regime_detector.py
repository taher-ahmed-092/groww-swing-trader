"""Regime classification behavior (pure classify, no network)."""
from __future__ import annotations

from src.data.regime_detector import RegimeDetector


def test_bull_regime_from_synthetic_data():
    ind = {"ma_50": 110, "ma_200": 100, "rsi_14": 60, "adx_14": 30, "atr_14": 2}
    out = RegimeDetector.classify(ind, current=120, recent_range=2)
    assert out["regime"] == "BULL_TRENDING"
    assert out["size_multiplier"] == 1.0


def test_bear_regime_from_synthetic_data():
    ind = {"ma_50": 95, "ma_200": 100, "rsi_14": 40, "adx_14": 30, "atr_14": 2}
    out = RegimeDetector.classify(ind, current=90, recent_range=2)
    assert out["regime"] == "BEAR_TRENDING"
    assert out["size_multiplier"] == 0.5


def test_choppy_regime():
    ind = {"ma_50": 100, "ma_200": 100, "rsi_14": 50, "adx_14": 15, "atr_14": 2}
    out = RegimeDetector.classify(ind, current=100, recent_range=1.0)  # < 0.8*ATR
    assert out["regime"] in ("RANGE_BOUND", "VOLATILE")
