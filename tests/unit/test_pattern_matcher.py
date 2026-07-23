"""PatternMatcher — recall-and-apply memory for a specific new candidate."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.memory.pattern_matcher import PatternMatcher


def _entry(pattern_id, description, confidence, regime="ANY", category="FORCED_LEARNING"):
    e = MagicMock()
    e.pattern_id = pattern_id
    e.pattern_description = description
    e.confidence = confidence
    e.observed_in_regime = regime
    e.category = category
    return e


def _matcher_with_kb(kb):
    pm = PatternMatcher.__new__(PatternMatcher)
    pm.journal = MagicMock()
    pm.journal.get_active_knowledge.return_value = kb
    return pm


def test_no_kb_returns_empty():
    pm = _matcher_with_kb([])
    result = pm.recall("RELIANCE", {"rsi_14": 56, "trend": "UPTREND"}, "BULL_TRENDING")
    assert result["adjustment"] == 0.0
    assert result["matches"] == []


def test_symbol_match_boosts_score():
    kb = [_entry("p1", "REPLAY WON: RELIANCE (large) | RSI 56 | UPTREND", 0.9, "BULL_TRENDING")]
    pm = _matcher_with_kb(kb)
    result = pm.recall("RELIANCE", {"rsi_14": 56, "trend": "UPTREND"}, "BULL_TRENDING")
    assert result["adjustment"] > 0


def test_symbol_match_penalizes_score():
    kb = [_entry("p1", "REPLAY LOST: RELIANCE (large) | RSI 56 | UPTREND", 0.9, "BULL_TRENDING")]
    pm = _matcher_with_kb(kb)
    result = pm.recall("RELIANCE", {"rsi_14": 56, "trend": "UPTREND"}, "BULL_TRENDING")
    assert result["adjustment"] < 0


def test_bounded_adjustment():
    kb = [_entry(f"p{i}", f"REPLAY WON: RELIANCE {i} UPTREND", 1.0, "BULL_TRENDING") for i in range(20)]
    pm = _matcher_with_kb(kb)
    result = pm.recall("RELIANCE", {"rsi_14": 56, "trend": "UPTREND"}, "BULL_TRENDING")
    assert -1.5 <= result["adjustment"] <= 1.5


def test_no_match_returns_neutral():
    kb = [_entry("p1", "REPLAY WON: TCS (large) | RSI 30 | DOWNTREND", 0.9, "BEAR_TRENDING")]
    pm = _matcher_with_kb(kb)
    result = pm.recall("RELIANCE", {"rsi_14": 56, "trend": "UPTREND"}, "BULL_TRENDING")
    assert abs(result["adjustment"]) < 0.5


def test_never_raises_on_malformed_entry():
    bad = MagicMock()
    bad.pattern_id = "bad"
    bad.pattern_description = None
    bad.confidence = None
    bad.observed_in_regime = None
    bad.category = None
    pm = _matcher_with_kb([bad])
    result = pm.recall("RELIANCE", {}, "UNKNOWN")
    assert "adjustment" in result
