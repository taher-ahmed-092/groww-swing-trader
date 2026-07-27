"""Risk checker behavior tests. These verify the hard gate, not mocks."""
from __future__ import annotations

from config.risk_limits import LIMITS
from config.settings import settings
from src.orchestrator.state import get_initial_state
from src.risk.checker import DEFAULT_PORTFOLIO_VALUE_INR, RiskChecker, check_capital_sanity


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
    # Low scores must be rejected on the confidence floor (message wording is
    # "score X < min Y"; threshold is 0.60 in demo mode, 0.80 for real money).
    assert any("score" in r and "min" in r for r in result["reasons"])


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


def test_default_portfolio_value_is_realistic_paper_capital():
    """Root-cause regression guard: DEFAULT_PORTFOLIO_VALUE_INR must track the
    configurable paper capital, never the old hardcoded ₹1500 that couldn't
    size 1 share of any large-cap stock."""
    assert DEFAULT_PORTFOLIO_VALUE_INR == settings.paper_capital_inr
    assert DEFAULT_PORTFOLIO_VALUE_INR >= 50000


def test_infy_like_trade_passes_risk_gate_at_realistic_capital():
    """1 share of an INFY-like stock (entry 1040, 7% stop -> risk ~72.8 INR)
    must clear the 3% max-risk-per-trade gate at realistic paper capital —
    it was mathematically impossible to size at the old ₹1500 default."""
    state = get_initial_state("INFY")
    entry = 1040.0
    stop = round(entry * (1 - LIMITS.stop_loss_pct / 100), 2)
    state["fundamental_verdict"] = {"score": 0.88, "proceed": True}
    state["technical_verdict"] = {
        "score": 0.90, "signal": "BUY", "entry_price": entry,
        "stop_price": stop, "target_price": entry * 1.15, "proceed": True,
    }
    state["judge_verdict"] = {"approved": True, "score": 0.85, "flags": []}

    result = RiskChecker(portfolio_value_inr=100000.0, open_positions=0).check(state)
    assert result["approved"] is True
    assert result["quantity"] >= 1
    # The exact rejection from the incident report should no longer fire.
    assert not any("risk" in r and "> max allowed" in r for r in result["reasons"])


def test_capital_sanity_flags_undersized_paper_capital():
    result = check_capital_sanity(portfolio_value_inr=1500.0, prices={"INFY": 1040.0})
    assert result["ok"] is False
    assert result["min_viable_capital"] > 1500.0
    assert "message" in result


def test_capital_sanity_passes_at_realistic_capital():
    result = check_capital_sanity(portfolio_value_inr=100000.0, prices={"INFY": 1040.0})
    assert result["ok"] is True
