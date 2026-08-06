"""classify_strategy() previously read indicator keys ("close", "ma_200",
"near_52w_high", "flags") that compute_indicators() never produces — it
outputs "trend" and "week52_position" instead. mean_reversion and breakout
could therefore never fire from any real call site (forced/cont-sim/
intraday/real all pass raw compute_indicators() output), and the Strategy
Performance table showed 0 trades for both despite real oversold-bounce and
breakout entries happening. Fixed to key on fields that actually exist."""
from __future__ import annotations

from src.analytics.strategy_attribution import classify_strategy


def test_oversold_not_in_downtrend_classifies_mean_reversion():
    indicators = {"rsi_14": 28, "trend": "SIDEWAYS", "volume_ratio": 1.0}
    assert classify_strategy(indicators) == "mean_reversion"


def test_oversold_in_confirmed_downtrend_is_not_mean_reversion():
    """A stock in a confirmed downtrend that's merely oversold isn't a
    reversion bounce candidate — still falls through to momentum."""
    indicators = {"rsi_14": 28, "trend": "DOWNTREND", "volume_ratio": 1.0}
    assert classify_strategy(indicators) == "momentum"


def test_near_52w_high_with_volume_surge_classifies_breakout():
    indicators = {"rsi_14": 60, "trend": "UPTREND", "week52_position": "NEAR_HIGH", "volume_ratio": 2.0}
    assert classify_strategy(indicators) == "breakout"


def test_near_52w_high_without_volume_surge_is_not_breakout():
    indicators = {"rsi_14": 60, "trend": "UPTREND", "week52_position": "NEAR_HIGH", "volume_ratio": 1.0}
    assert classify_strategy(indicators) == "momentum"


def test_plain_uptrend_is_momentum():
    indicators = {"rsi_14": 55, "trend": "UPTREND", "week52_position": "MID", "volume_ratio": 1.1}
    assert classify_strategy(indicators) == "momentum"


def test_is_pairs_flag_overrides_everything():
    indicators = {"rsi_14": 28, "trend": "DOWNTREND"}
    assert classify_strategy(indicators, is_pairs=True) == "pairs_trading"
