"""Regime-stratified historical replay (Fix 3) — Nifty-window classification,
progress tracking, and regime rotation. Network calls (yfinance) are stubbed."""
from __future__ import annotations

import src.learning.historical_replay as hr_mod
from src.learning.historical_replay import (
    REGIME_RECORDS_TARGET,
    REGIME_TARGETS,
    HistoricalReplayEngine,
)


def test_classify_nifty_window_bull_trending():
    assert HistoricalReplayEngine._classify_nifty_window(7.0, 1.0) == "BULL_TRENDING"


def test_classify_nifty_window_bear_trending():
    assert HistoricalReplayEngine._classify_nifty_window(-8.0, 2.0) == "BEAR_TRENDING"


def test_classify_nifty_window_range_bound():
    assert HistoricalReplayEngine._classify_nifty_window(0.5, 0.8) == "RANGE_BOUND"


def test_classify_nifty_window_transitional_fallback():
    assert HistoricalReplayEngine._classify_nifty_window(3.0, 2.5) == "TRANSITIONAL"


def test_replay_regime_stratified_rejects_unknown_regime(tmp_path, monkeypatch):
    monkeypatch.setattr(hr_mod, "REGIME_REPLAY_PROGRESS_FILE", tmp_path / "progress.json")
    engine = HistoricalReplayEngine()
    result = engine.replay_regime_stratified("NOT_A_REGIME")
    assert result["patterns_added"] == 0
    assert "error" in result


def test_replay_regime_stratified_no_windows_found(tmp_path, monkeypatch):
    monkeypatch.setattr(hr_mod, "REGIME_REPLAY_PROGRESS_FILE", tmp_path / "progress.json")
    engine = HistoricalReplayEngine()
    monkeypatch.setattr(engine, "find_regime_windows", lambda regime, **k: [])
    result = engine.replay_regime_stratified("BEAR_TRENDING")
    assert result == {"replayed": 0, "signals_found": 0, "wins": 0, "losses": 0,
                      "patterns_added": 0, "regime": "BEAR_TRENDING"}


def test_regime_progress_persists_and_accumulates(tmp_path, monkeypatch):
    monkeypatch.setattr(hr_mod, "REGIME_REPLAY_PROGRESS_FILE", tmp_path / "progress.json")
    engine = HistoricalReplayEngine()
    engine._update_regime_progress("BULL_TRENDING", 10)
    engine._update_regime_progress("BULL_TRENDING", 15)
    stats = engine.get_regime_replay_stats()
    assert stats["BULL_TRENDING"]["records"] == 25
    assert stats["BULL_TRENDING"]["target"] == REGIME_RECORDS_TARGET
    assert stats["BULL_TRENDING"]["complete"] is False
    for regime in REGIME_TARGETS:
        assert regime in stats


def test_next_regime_to_replay_picks_furthest_below_target(tmp_path, monkeypatch):
    monkeypatch.setattr(hr_mod, "REGIME_REPLAY_PROGRESS_FILE", tmp_path / "progress.json")
    engine = HistoricalReplayEngine()
    engine._update_regime_progress("BULL_TRENDING", 90)
    engine._update_regime_progress("RANGE_BOUND", 50)
    # BEAR_TRENDING and TRANSITIONAL have 0 records — one of those goes next.
    assert engine.next_regime_to_replay() in ("BEAR_TRENDING", "TRANSITIONAL")


def test_update_knowledge_namespaces_pattern_id_by_regime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine = HistoricalReplayEngine()
    ind = {"rsi_14": 55, "trend": "UPTREND", "adx_signal": "TRENDING"}
    signal = {"action": "BUY", "score": 0.5}
    engine._update_knowledge("TESTCO", "large", ind, signal, "WIN", 2.0,
                             "desc", "2026-01-01", market_regime="BEAR_TRENDING")
    kb = engine.journal.get_active_knowledge(min_confidence=0.0)
    assert any(e.pattern_id.startswith("replay-regimebear_trending-") for e in kb)
