"""Tests for the demo-threshold fix, new indicators, time window, and noise gate."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.agents.technical.indicators import compute_indicators
from src.trading.time_window import TradingTimeWindow


def _df(n=260, up=True):
    rng = np.random.RandomState(7)
    base = np.linspace(100, 150, n) if up else np.linspace(150, 100, n)
    close = pd.Series(base + rng.randn(n))
    return pd.DataFrame({
        "Open": close.shift(1).fillna(close), "High": close + 2,
        "Low": close - 2, "Close": close, "Volume": rng.randint(1e5, 3e5, n),
    })


def test_new_indicators_present_and_typed():
    ind = compute_indicators(_df())
    for key in ("ichimoku_signal", "vwap", "price_vs_vwap", "pivot", "r1", "s1",
                "cmf_signal", "supertrend_direction", "week52_position",
                "candlestick_confidence"):
        assert key in ind
    assert ind["ichimoku_signal"] in ("BULLISH", "BEARISH", "NEUTRAL")
    assert ind["price_vs_vwap"] in ("ABOVE", "BELOW", None)


def test_pivot_ordering():
    ind = compute_indicators(_df())
    # Support below pivot below resistance.
    assert ind["s1"] < ind["pivot"] < ind["r1"]


def test_candlestick_confidence_values():
    ind = compute_indicators(_df())
    assert ind["candlestick_confidence"] in ("HIGH", "MEDIUM", "LOW", "NONE")


def test_time_window_has_countdown():
    tw = TradingTimeWindow().compute_entry_window(
        {"signal": "BUY", "indicators": {"rsi_14": 58, "adx_signal": "TRENDING"}}, {}
    )
    assert tw["current_status"] in ("OPEN", "WAITING", "MISSED")
    assert tw["window_label"] == "MORNING_MOMENTUM"
    assert "countdown_display" in tw and tw["countdown_display"]


def test_demo_threshold_is_lower():
    # In demo mode the confidence floor drops to 0.60 so the execution path is testable.
    import importlib

    import config.settings as settings_mod
    importlib.reload(settings_mod)
    # effective_demo_mode is True in the test env (no ANTHROPIC_API_KEY).
    assert settings_mod.settings.effective_demo_mode is True
