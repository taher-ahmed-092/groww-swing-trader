"""Tiered stop manager behavior."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from config.risk_limits import LIMITS
from src.risk.tiered_stops import TieredStopManager


def _df(entry=100.0, n=60):
    rng = np.random.RandomState(0)
    close = pd.Series(np.linspace(entry * 0.9, entry, n) + rng.randn(n) * 0.5)
    return pd.DataFrame({
        "Open": close, "High": close + 1, "Low": close - 1,
        "Close": close, "Volume": rng.randint(1e5, 2e5, n),
    })


def _trade(entry=100.0, current_stop=93.0, partial_exited=False):
    return SimpleNamespace(entry_price=entry, stop_price=current_stop,
                           current_stop=current_stop, partial_exited=partial_exited)


def test_initial_stop_below_entry():
    out = TieredStopManager().calculate_initial_stops(100.0, _df())
    assert out["hard_stop"] < 100.0


def test_stop_never_exceeds_7pct():
    out = TieredStopManager().calculate_initial_stops(100.0, _df())
    assert out["hard_stop_pct"] <= LIMITS.stop_loss_pct + 1e-6


def test_breakeven_triggers_at_3pct():
    action = TieredStopManager().update_stops_for_held_position(_trade(), 103.5)
    assert action["action"] == "BREAKEVEN_STOP"
    assert action["new_stop"] == 100.0


def test_trailing_triggers_at_5pct():
    # partial already taken so the trailing branch is reached.
    action = TieredStopManager().update_stops_for_held_position(
        _trade(current_stop=100.0, partial_exited=True), 106.0
    )
    assert action["action"] == "TRAILING_STOP"
    assert action["new_stop"] > 100.0


def test_partial_exit_at_5pct():
    action = TieredStopManager().update_stops_for_held_position(_trade(), 105.5)
    assert action["action"] == "PARTIAL_EXIT"
