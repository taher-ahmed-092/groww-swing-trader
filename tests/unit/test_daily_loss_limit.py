"""Daily loss circuit breaker — halts all sources at -6% cumulative net P&L."""
from __future__ import annotations

from unittest.mock import patch

from src.orchestrator.state import get_initial_state
from src.risk.checker import RiskChecker


def _approved_state():
    state = get_initial_state("RELIANCE")
    state["technical_verdict"] = {
        "score": 0.9, "entry_price": 100.0, "stop_price": 95.0, "target_price": 110.0,
    }
    state["fundamental_verdict"] = {"score": 0.9}
    state["judge_verdict"] = {"approved": True, "overall_score": 8.0}
    return state


def test_halt_on_minus_6pct(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("src.risk.checker._todays_cumulative_net_pnl_pct", return_value=-7.0):
        result = RiskChecker().check(_approved_state())
    assert result["approved"] is False
    assert any("DAILY_LOSS_LIMIT" in r for r in result["reasons"])


def test_no_halt_below_threshold(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("src.risk.checker._todays_cumulative_net_pnl_pct", return_value=-2.0), \
         patch("src.risk.checker.is_daily_loss_halted", return_value=False):
        result = RiskChecker().check(_approved_state())
    assert result["approved"] is True
