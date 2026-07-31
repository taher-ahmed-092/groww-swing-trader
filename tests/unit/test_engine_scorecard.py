"""EngineScorecard — per-engine profit factor, auto-throttle/pause/restore."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.analytics import era
from src.analytics.strategy_scorecard import THROTTLE_FILE, EngineScorecard


@pytest.fixture(autouse=True)
def _isolate_era_marker(tmp_path, monkeypatch):
    # ERA_MARKER_FILE is anchored to the repo root (not cwd), so chdir alone
    # no longer isolates it — every test in this file must patch it directly.
    monkeypatch.setattr(era, "ERA_MARKER_FILE", tmp_path / "era_marker.json")


def _write_forced_history(n_wins: int, n_losses: int, pnl_win: float, pnl_loss: float) -> None:
    trades = (
        [{"outcome": "WIN", "pnl_pct": pnl_win, "trade_type": "LIVE_FORCED"}] * n_wins
        + [{"outcome": "LOSS", "pnl_pct": pnl_loss, "trade_type": "LIVE_FORCED"}] * n_losses
    )
    p = Path("data/cache/forced_trades_history.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(trades))


def test_scorecard_pauses_low_profit_factor(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 20 wins @ +2.0%, 40 losses @ -3.0% -> PF = 40/120 = 0.33 < 0.5, n=60
    _write_forced_history(n_wins=20, n_losses=40, pnl_win=2.0, pnl_loss=-3.0)
    changes = EngineScorecard().apply_throttles()
    assert changes["LIVE_FORCED"]["new"] == "paused"
    saved = json.loads(THROTTLE_FILE.read_text())
    assert saved["LIVE_FORCED"]["mode"] == "paused"


def test_scorecard_restores_high_profit_factor(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    THROTTLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    THROTTLE_FILE.write_text(json.dumps(
        {"LIVE_FORCED": {"mode": "paused", "pf": 0.3, "updated": "x", "reason": "seed"}}))
    # 40 wins @ +3.0%, 20 losses @ -2.0% -> PF = 120/40 = 3.0 > 1.2, n=60
    _write_forced_history(n_wins=40, n_losses=20, pnl_win=3.0, pnl_loss=-2.0)
    changes = EngineScorecard().apply_throttles()
    assert changes["LIVE_FORCED"]["new"] == "normal"


def test_scorecard_probation_below_20_current_era_trades(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Only 10 trades — below PROBATION_MIN_TRADES (20): the engine is still
    # building current-era evidence, so it goes to "probation" (half-size
    # entries), not silently staying "normal" or getting stuck "paused".
    _write_forced_history(n_wins=1, n_losses=9, pnl_win=1.0, pnl_loss=-5.0)
    changes = EngineScorecard().apply_throttles()
    assert changes["LIVE_FORCED"]["new"] == "probation"


def test_scorecard_neutral_pf_waits_for_50_trades(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 30 trades, PF exactly 1.0 (neutral band, between throttle 0.8 and
    # restore 1.2) — past probation (20) but below MIN_TRADES_FOR_THROTTLE
    # (50): genuinely ambiguous evidence, correctly waits rather than
    # snapping to "normal" on a thin sample.
    _write_forced_history(n_wins=15, n_losses=15, pnl_win=1.0, pnl_loss=-1.0)
    changes = EngineScorecard().apply_throttles()
    assert "LIVE_FORCED" not in changes


def test_scorecard_strong_pf_exits_probation_at_20_trades(tmp_path, monkeypatch):
    """Bug fix: PROBATION_MIN_TRADES=20 is documented (and relied on by
    daily_learning_job's messaging) as the point an engine's PF starts
    getting evaluated — the old code instead silently required 50 trades
    before a pause/throttle/restore decision could fire, so a genuinely
    strong (or weak) engine sat in "probation" for 30 extra trades past its
    documented exit point. A PF clear of PF_RESTORE_THRESHOLD (1.2) at
    exactly 20 trades must promote to "normal" immediately."""
    monkeypatch.chdir(tmp_path)
    THROTTLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    THROTTLE_FILE.write_text(json.dumps(
        {"LIVE_FORCED": {"mode": "probation", "pf": None, "updated": "x", "reason": "seed"}}))
    # 13 wins @ +3.0%, 7 losses @ -2.0%, n=20 -> PF = 39/14 = 2.79 > 1.2.
    _write_forced_history(n_wins=13, n_losses=7, pnl_win=3.0, pnl_loss=-2.0)
    changes = EngineScorecard().apply_throttles()
    assert changes["LIVE_FORCED"]["new"] == "normal"


def test_scorecard_weak_pf_pauses_at_20_trades(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 4 wins @ +2.0%, 16 losses @ -3.0%, n=20 -> PF = 8/48 = 0.17 < 0.5.
    # A weak engine must not get 30 more trades of unthrottled volume just
    # because it hasn't reached the 50-trade full-evidence bar.
    _write_forced_history(n_wins=4, n_losses=16, pnl_win=2.0, pnl_loss=-3.0)
    changes = EngineScorecard().apply_throttles()
    assert changes["LIVE_FORCED"]["new"] == "paused"


def test_get_mode_defaults_to_normal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert EngineScorecard.get_mode("LIVE_FORCED") == "normal"


def test_historical_sim_is_retired(tmp_path, monkeypatch):
    """HISTORICAL_SIM is retired on scorecard evidence regardless of its
    current-era trade count or profit factor — always_on_trader.py no longer
    places trades through it, but the row stays visible on the scorecard."""
    monkeypatch.chdir(tmp_path)
    p = Path("data/cache/forced_trades_history.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        [{"outcome": "WIN", "pnl_pct": 1.0, "trade_type": "HISTORICAL_SIM"}] * 5))

    changes = EngineScorecard().apply_throttles()

    assert changes["HISTORICAL_SIM"]["new"] == "retired"
    saved = json.loads(THROTTLE_FILE.read_text())
    assert saved["HISTORICAL_SIM"]["mode"] == "retired"


def test_retirement_logged_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    scorecard = EngineScorecard()
    first = scorecard.apply_throttles()
    assert "HISTORICAL_SIM" in first
    # Second run: already retired, must not re-log the same retirement.
    second = scorecard.apply_throttles()
    assert "HISTORICAL_SIM" not in second


def test_stale_pause_reevaluates_to_probation_on_compute(tmp_path, monkeypatch):
    """A "paused" state older than STALE_THROTTLE_HOURS must not sit deadlocked
    until the next scheduled apply_throttles() run — compute() (called by the
    dashboard on every poll) re-evaluates it immediately."""
    import json
    from datetime import datetime, timedelta

    from src.analytics.strategy_scorecard import IST

    monkeypatch.chdir(tmp_path)
    stale_ts = (datetime.now(IST) - timedelta(hours=13)).isoformat()
    THROTTLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    THROTTLE_FILE.write_text(json.dumps(
        {"LIVE_FORCED": {"mode": "paused", "pf": 0.2, "updated": stale_ts, "reason": "seed"}}))
    # No current-era trades -> compute()'s re-eval should land on "probation".
    EngineScorecard().compute()
    saved = json.loads(THROTTLE_FILE.read_text())
    assert saved["LIVE_FORCED"]["mode"] == "probation"


def test_fresh_pause_not_reevaluated_on_compute(tmp_path, monkeypatch):
    """A recently-updated "paused" state (< STALE_THROTTLE_HOURS) must be left
    alone by compute() — only the scheduled apply_throttles() job (or a
    genuinely stale state) should change it."""
    import json
    from datetime import datetime, timedelta

    from src.analytics.strategy_scorecard import IST

    monkeypatch.chdir(tmp_path)
    fresh_ts = (datetime.now(IST) - timedelta(hours=1)).isoformat()
    THROTTLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    THROTTLE_FILE.write_text(json.dumps(
        {"LIVE_FORCED": {"mode": "paused", "pf": 0.2, "updated": fresh_ts, "reason": "seed"}}))
    EngineScorecard().compute()
    saved = json.loads(THROTTLE_FILE.read_text())
    assert saved["LIVE_FORCED"]["mode"] == "paused"
