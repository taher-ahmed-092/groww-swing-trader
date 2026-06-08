"""Risk checker behavior tests. These verify the hard gate, not mocks."""
from __future__ import annotations

from config.risk_limits import LIMITS
from src.orchestrator.state import get_initial_state
from src.risk.checker import RiskChecker


def _approved_state():
    """A well-formed state that should pass every risk check."""
    state = get_initial_state("RELIANCE")
    entry = 100.0
    state["fundamental_verdict"] = {"score": 0.88, "proceed": True}
    state["technical_verdict"] = {
        "score": 0.90,
        "signal": "BUY",
        "entry_price": entry,
        "stop_price": round(entry * (1 - LIMITS.stop_loss_pct / 100), 4),
        "target_price": 120.0,
        "proceed": True,
    }
    state["judge_verdict"] = {"approved": True, "score": 0.85, "flags": []}
    return state


def test_no_stop_price_rejected():
    state = _approved_state()
    state["technical_verdict"]["stop_price"] = 0
    result = RiskChecker(open_positions=0).check(state)
    assert result["approved"] is False
    assert any("stop" in r.lower() for r in result["reasons"])


def test_low_confidence_rejected():
    state = _approved_state()
    state["fundamental_verdict"]["score"] = 0.5
    state["technical_verdict"]["score"] = 0.5
    result = RiskChecker(open_positions=0).check(state)
    assert result["approved"] is False
    assert any("min_confidence" in r for r in result["reasons"])


def test_too_many_open_positions_rejected():
    state = _approved_state()
    result = RiskChecker(open_positions=LIMITS.max_open_positions).check(state)
    assert result["approved"] is False
    assert any("open positions" in r.lower() for r in result["reasons"])


def test_valid_trade_approved():
    state = _approved_state()
    result = RiskChecker(open_positions=0).check(state)
    assert result["approved"] is True
    assert result["quantity"] >= 1
    assert result["risk_inr"] > 0
