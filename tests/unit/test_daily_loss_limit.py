"""Daily loss circuit breaker — halts REAL trading only, at -6% cumulative
REAL net P&L. Simulation/forced/intraday P&L must never trigger it — see
_todays_real_trades()/_todays_cumulative_net_pnl_pct() in src/risk/checker.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

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


def _fake_real_trades(n=1):
    return [MagicMock(outcome="LOSS") for _ in range(n)]


def test_halt_on_minus_6pct(tmp_path, monkeypatch):
    """With >=1 real trade closed today and cumulative net P&L below -6%,
    the breaker must halt."""
    monkeypatch.chdir(tmp_path)
    with patch("src.risk.checker._todays_real_trades", return_value=_fake_real_trades()), \
         patch("src.risk.checker._todays_cumulative_net_pnl_pct", return_value=-7.0):
        result = RiskChecker().check(_approved_state())
    assert result["approved"] is False
    assert any("DAILY_LOSS_LIMIT" in r for r in result["reasons"])


def test_no_halt_below_threshold(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("src.risk.checker._todays_real_trades", return_value=_fake_real_trades()), \
         patch("src.risk.checker._todays_cumulative_net_pnl_pct", return_value=-2.0), \
         patch("src.risk.checker.is_daily_loss_halted", return_value=False):
        result = RiskChecker().check(_approved_state())
    assert result["approved"] is True


def test_zero_real_trades_never_halts_even_if_cumulative_deeply_negative(tmp_path, monkeypatch):
    """The core bug fix: with 0 real trades today, the breaker cannot fire —
    no real capital has been deployed — even if is_daily_loss_halted() is
    stuck True from a stale/bugged halt file, and even if a (patched)
    cumulative P&L reading is deeply negative. This is the exact bug that let
    forced-simulation losses (-21% net on a single day) block real signal
    trades (INFY pairs 8.4/10, AJANTPHARM technical=0.97) with 0 real trades
    ever placed."""
    monkeypatch.chdir(tmp_path)
    with patch("src.risk.checker._todays_real_trades", return_value=[]), \
         patch("src.risk.checker._todays_cumulative_net_pnl_pct", return_value=-50.0), \
         patch("src.risk.checker.is_daily_loss_halted", return_value=True):
        result = RiskChecker().check(_approved_state())
    assert result["approved"] is True


def test_spurious_halt_file_cleared_when_zero_real_trades(tmp_path, monkeypatch):
    """A halt file with 0 real trades today is spurious and must be removed."""
    monkeypatch.chdir(tmp_path)
    halt_file = tmp_path / "data" / "cache" / "daily_loss_halt.txt"
    halt_file.parent.mkdir(parents=True, exist_ok=True)
    halt_file.write_text("2020-01-01")
    monkeypatch.setattr("src.risk.checker.DAILY_LOSS_HALT_FILE", halt_file)
    with patch("src.risk.checker._todays_real_trades", return_value=[]):
        RiskChecker().check(_approved_state())
    assert not halt_file.exists()
