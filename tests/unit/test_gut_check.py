"""Gut-check agent rule-based behavior (no API key in test env → rule path)."""
from __future__ import annotations

from src.agents.gut_check import GutCheckAgent


def _state(fund, tech, judge, manually=False, nifty="UPTREND"):
    return {
        "symbol": "TESTCO",
        "fundamental_verdict": {"score": fund},
        "technical_verdict": {"score": tech},
        "judge_verdict": {"overall_score": judge, "flags": []},
        "market_context": {"nifty_trend": nifty},
        "manually_requested": manually,
    }


def test_rule_based_gut_high_scores():
    out = GutCheckAgent().check(_state(0.85, 0.85, 8.5))
    assert out["gut_score"] >= 7


def test_rule_based_gut_low_scores():
    out = GutCheckAgent().check(_state(0.4, 0.4, 5.0))
    assert out["gut_score"] <= 5


def test_manually_requested_adds_concern():
    out = GutCheckAgent().check(_state(0.7, 0.7, 7.0, manually=True))
    assert any("emotional bias" in c.lower() for c in out["gut_concerns"])


def test_never_raises():
    out = GutCheckAgent().check({})
    assert isinstance(out, dict)
    assert "gut_score" in out
