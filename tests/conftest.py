"""
Shared test isolation.

RiskChecker's daily-loss circuit breaker (src/risk/checker.py) reads real
accumulated trade history from data/cache/*.json and data/journal/trades.db —
by design, since it must see every source's real P&L. That's correct for
production but means any test hitting RiskChecker.check() would otherwise be
at the mercy of whatever this dev environment has accumulated (158+ simulated
trades and counting). Neutralize it by default; tests that specifically
exercise the circuit breaker (tests/unit/test_daily_loss_limit.py) override
this via their own monkeypatch calls, which take precedence within that test.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_daily_loss_halt_by_default(monkeypatch):
    monkeypatch.setattr("src.risk.checker.is_daily_loss_halted", lambda: False)
    monkeypatch.setattr("src.risk.checker._todays_cumulative_net_pnl_pct", lambda: 0.0)
