"""
Shared test isolation.

RiskChecker's daily-loss circuit breaker (src/risk/checker.py) reads REAL
TradingJournal trades only (never forced/intraday/short simulation history —
that was the bug: simulation P&L used to count toward this and could halt
real signal evaluation with 0 real trades ever placed). Even so, any test
hitting RiskChecker.check() would otherwise be at the mercy of whatever real
trades this dev environment's journal DB has accumulated. Neutralize it by
default; tests that specifically exercise the circuit breaker
(tests/unit/test_daily_loss_limit.py) override this via their own monkeypatch
calls, which take precedence within that test.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_daily_loss_halt_by_default(monkeypatch):
    monkeypatch.setattr("src.risk.checker.is_daily_loss_halted", lambda: False)
    monkeypatch.setattr("src.risk.checker._todays_real_trades", lambda journal=None: [])
    monkeypatch.setattr("src.risk.checker._todays_cumulative_net_pnl_pct", lambda journal=None: 0.0)
