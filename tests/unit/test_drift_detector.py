"""Concept drift detector — win-rate drift, regime drift, and the confidence
decay response, all isolated to a tmp_path cache dir + fresh journal DB."""
from __future__ import annotations

import json
from pathlib import Path

from src.analytics.drift_detector import DriftDetector
from src.memory.journal import KnowledgeEntry


def _write_sims(n_win_first_half, n_win_second_half, regime="VOLATILE"):
    """30 sims in each half; win/loss ratio per half controlled by caller."""
    trades = []
    for i in range(30):
        outcome = "WIN" if i < n_win_first_half else "LOSS"
        trades.append({"outcome": outcome, "regime": regime, "rsi": 52,
                       "simulated_at": f"2026-01-{i + 1:02d}T00:00:00"})
    for i in range(30):
        outcome = "WIN" if i < n_win_second_half else "LOSS"
        trades.append({"outcome": outcome, "regime": regime, "rsi": 52,
                       "simulated_at": f"2026-02-{i + 1:02d}T00:00:00"})
    p = Path("data/cache/forced_trades_history.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(trades))


def test_no_drift_stable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sims(n_win_first_half=15, n_win_second_half=15)  # 50% both halves
    result = DriftDetector()._check_wr_drift()
    assert result["detected"] is False


def test_wr_drift_detected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Previous half: 20/30 wins (67%). Recent half: 5/30 wins (17%) — 50pp drop.
    _write_sims(n_win_first_half=20, n_win_second_half=5)
    result = DriftDetector()._check_wr_drift()
    assert result["detected"] is True
    assert result["drop"] >= 15


def test_regime_drift_detected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sims(n_win_first_half=15, n_win_second_half=15, regime="VOLATILE")

    import src.data.regime_detector as rd
    monkeypatch.setattr(rd.RegimeDetector, "detect", lambda self: {"regime": "BULL_TRENDING"})

    result = DriftDetector()._check_regime_drift()
    assert result["detected"] is True
    assert result["trained_on"] == "VOLATILE"
    assert result["current"] == "BULL_TRENDING"


def test_confidence_decay_applied(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Force wr_drift by seeding a sharp win-rate collapse.
    _write_sims(n_win_first_half=20, n_win_second_half=5)

    import src.data.regime_detector as rd
    monkeypatch.setattr(rd.RegimeDetector, "detect", lambda self: {"regime": "VOLATILE"})

    detector = DriftDetector()
    detector.journal.log_knowledge_entry(KnowledgeEntry(
        pattern_id="test-pattern", pattern_description="test", confidence=0.80,
        observed_count=15,
    ))

    results = detector.check_and_respond()
    assert results["any_drift"] is True

    kb = detector.journal.get_active_knowledge(min_confidence=0.0)
    entry = next(e for e in kb if e.pattern_id == "test-pattern")
    assert entry.confidence < 0.80
    assert abs(entry.confidence - 0.80 * 0.80) < 1e-6
