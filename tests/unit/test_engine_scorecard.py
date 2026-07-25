"""EngineScorecard — per-engine profit factor, auto-throttle/pause/restore."""
from __future__ import annotations

import json
from pathlib import Path

from src.analytics.strategy_scorecard import THROTTLE_FILE, EngineScorecard


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


def test_scorecard_no_change_between_20_and_50_trades(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 30 trades — past probation (20) but below MIN_TRADES_FOR_THROTTLE (50):
    # not enough evidence yet for a pause/throttle/restore decision.
    _write_forced_history(n_wins=10, n_losses=20, pnl_win=1.0, pnl_loss=-5.0)
    changes = EngineScorecard().apply_throttles()
    assert "LIVE_FORCED" not in changes


def test_get_mode_defaults_to_normal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert EngineScorecard.get_mode("LIVE_FORCED") == "normal"
