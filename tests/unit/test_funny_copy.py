"""Funny regime copy — grounded humor, never crashes."""
from __future__ import annotations

from src.utils.funny_copy import REGIME_JOKES, get_regime_joke


def test_returns_string_for_known_regime():
    for regime in REGIME_JOKES:
        joke = get_regime_joke(regime)
        assert isinstance(joke, str)
        assert len(joke) > 0


def test_returns_fallback_for_unknown_regime():
    joke = get_regime_joke("SOME_MADE_UP_REGIME")
    assert isinstance(joke, str)
    assert len(joke) > 0


def test_never_raises():
    for regime in (None, "", "UNKNOWN", 123, [], "BULL_TRENDING"):
        get_regime_joke(regime)  # must not raise
