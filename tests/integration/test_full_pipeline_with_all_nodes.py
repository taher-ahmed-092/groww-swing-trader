"""
Integration: the upgraded pipeline wires all nodes, and the risk/technical outputs
carry the new dynamic-sizing / entry-recommendation / regime fields.

These assert structural invariants that hold without live network/approval, so they
are deterministic in CI.
"""
from __future__ import annotations

from src.agents.technical.entry_price import recommend_entry
from src.data.regime_detector import RegimeDetector
from src.orchestrator.graph import build_graph
from src.risk.checker import RiskChecker


def test_pipeline_includes_gut_check_node():
    graph = build_graph()
    assert "gut_check_node" in graph.nodes


def _approved_state():
    entry = 100.0
    return {
        "symbol": "TESTCO",
        "fundamental_verdict": {"score": 0.85, "proceed": True, "hard_rejected": False},
        "technical_verdict": {
            "score": 0.85, "signal": "BUY", "proceed": True, "entry_price": entry,
            "stop_price": 93.0, "target_price": 114.0, "weekly_trend": "UPTREND",
            "indicators": {"adx_signal": "TRENDING"},
        },
        "judge_verdict": {"approved": True, "overall_score": 8.0, "flags": []},
        "gut_check": {"gut_score": 7.5, "modifies_decision": False},
        "market_context": {"nifty_trend": "UPTREND", "size_multiplier": 1.0},
        "manually_requested": False,
    }


def test_dynamic_sizing_in_risk_output():
    result = RiskChecker(open_positions=0).check(_approved_state())
    assert result["approved"] is True
    for key in ("position_size_inr", "kelly_fraction", "confidence_tier"):
        assert key in result


def test_entry_recommendation_in_state():
    rec = recommend_entry({"ma_50": 100.0, "atr_14": 2.0}, 100.5)
    assert "entry_zone_low" in rec and "entry_zone_high" in rec


def test_regime_in_state():
    out = RegimeDetector.classify(
        {"ma_50": 110, "ma_200": 100, "rsi_14": 60, "adx_14": 30, "atr_14": 2},
        current=120, recent_range=2,
    )
    assert out["regime"] == "BULL_TRENDING"
    assert "size_multiplier" in out
