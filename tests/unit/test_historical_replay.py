"""Historical replay engine — signal eval, outcome verification, rotation."""
from __future__ import annotations

import src.learning.historical_replay as hr_mod
from src.data.watchlist import ALL_STOCKS
from src.learning.historical_replay import HistoricalReplayEngine


def test_evaluate_signal_uptrend_buy():
    ind = {"rsi_14": 58, "trend": "UPTREND", "adx_signal": "TRENDING"}
    assert HistoricalReplayEngine._evaluate_signal(ind, "large")["action"] == "BUY"


def test_evaluate_signal_downtrend_avoid():
    ind = {"rsi_14": 40, "trend": "DOWNTREND", "adx_signal": "NEUTRAL"}
    assert HistoricalReplayEngine._evaluate_signal(ind, "large")["action"] in ("AVOID", "SKIP")


def test_check_actual_outcome_win():
    outcome, exit_price, _ = HistoricalReplayEngine._check_actual_outcome(
        100, 93, 114,
        future_highs=[108, 112, 115, 110, 108],
        future_lows=[105, 108, 112, 107, 106],
        future_closes=[107, 111, 114, 109, 107])
    assert outcome == "WIN"
    assert exit_price == 114


def test_check_actual_outcome_loss():
    outcome, exit_price, _ = HistoricalReplayEngine._check_actual_outcome(
        100, 93, 114,
        future_highs=[97, 95, 96, 94, 93],
        future_lows=[92, 90, 91, 89, 88],
        future_closes=[95, 92, 93, 91, 90])
    assert outcome == "LOSS"
    assert exit_price == 93


def test_replay_no_crash_on_bad_symbol(monkeypatch):
    engine = HistoricalReplayEngine()
    monkeypatch.setattr(engine.fetcher, "get_price_history", lambda *a, **k: None)
    out = engine._replay_stock("BADSTOCK999", "large", 3)
    assert out == {"signals_found": 0, "wins": 0, "losses": 0, "patterns_added": 0}


def test_pick_stocks_rotates_all(tmp_path, monkeypatch):
    monkeypatch.setattr(hr_mod, "REPLAY_CACHE", tmp_path / "progress.json")
    engine = HistoricalReplayEngine()
    engine._update_progress(list(ALL_STOCKS.keys()))
    assert len(engine._load_progress()) == len(ALL_STOCKS)
    assert engine.get_stats()["stocks_pending"] == 0


def test_stats_returns_dict():
    stats = HistoricalReplayEngine().get_stats()
    for key in ("total_stocks_replayed", "replay_patterns_in_kb", "stocks_pending"):
        assert key in stats


def test_run_batch_returns_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(hr_mod, "REPLAY_CACHE", tmp_path / "progress.json")
    monkeypatch.setattr(hr_mod.time, "sleep", lambda *_a: None)
    engine = HistoricalReplayEngine()
    monkeypatch.setattr(engine, "_replay_stock", lambda *a, **k: {
        "signals_found": 0, "wins": 0, "losses": 0, "patterns_added": 0})
    out = engine.run_batch(n_stocks=2, n_days_each=1)
    for key in ("replayed", "signals_found", "wins", "losses", "patterns_added"):
        assert key in out
    assert out["replayed"] == 2
