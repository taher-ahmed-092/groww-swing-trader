"""Drift-detector decay cooldown and WR-drift persistence (2-consecutive-check
gate) — see test_drift_detector.py for the underlying detection-math tests.

Audit finding: confidence decay fired twice in 6h (00:00 and 06:00, ~40
patterns each) — decaying the same patterns repeatedly means none of them can
ever earn confidence back through fresh evidence. Two fixes: a 24h decay
cooldown, and requiring the WR-drift signal to persist across 2 consecutive
checks (a single 30-trade rolling-window dip is noise at this volume).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from src.analytics.drift_detector import DriftDetector


def _detector_with_forced_wr_drift(monkeypatch, decay_state_file, drift_history_file):
    import src.analytics.drift_detector as dd

    monkeypatch.setattr(dd, "DECAY_STATE_FILE", decay_state_file)
    monkeypatch.setattr(dd, "DRIFT_HISTORY_FILE", drift_history_file)
    detector = DriftDetector()
    # Force every raw check's WR-drift signal so the test isolates the
    # cooldown/persistence logic from the underlying evidence-gating math.
    monkeypatch.setattr(detector, "_check_wr_drift", lambda: {"detected": True, "message": "drift"})
    monkeypatch.setattr(detector, "_check_pattern_drift", lambda: {"detected": False})
    monkeypatch.setattr(detector, "_check_regime_drift", lambda: {"detected": False})
    return detector


def test_wr_drift_requires_two_consecutive_checks(tmp_path, monkeypatch):
    decay_state = tmp_path / "drift_decay_state.json"
    drift_history = tmp_path / "drift_history.json"
    detector = _detector_with_forced_wr_drift(monkeypatch, decay_state, drift_history)

    decay_calls = []
    monkeypatch.setattr(detector, "_apply_confidence_decay", lambda: decay_calls.append(1))

    first = detector.check_and_respond()
    assert first["wr_drift"]["confirmed"] is False
    assert decay_calls == []  # single detection — not confirmed, no decay

    second = detector.check_and_respond()
    assert second["wr_drift"]["confirmed"] is True
    assert decay_calls == [1]  # second consecutive detection — confirmed, decays


def test_decay_cooldown_prevents_repeat_within_24h(tmp_path, monkeypatch):
    decay_state = tmp_path / "drift_decay_state.json"
    drift_history = tmp_path / "drift_history.json"
    detector = _detector_with_forced_wr_drift(monkeypatch, decay_state, drift_history)

    decay_calls = []
    monkeypatch.setattr(detector, "_apply_confidence_decay", lambda: decay_calls.append(1))

    detector.check_and_respond()  # streak=1, no decay
    detector.check_and_respond()  # streak=2, confirmed -> decays
    assert decay_calls == [1]

    third = detector.check_and_respond()  # streak=3, confirmed again, but cooldown active
    assert third["wr_drift"]["confirmed"] is True
    assert third.get("decay_skipped_cooldown") is True
    assert decay_calls == [1]  # no second decay call


def test_decay_fires_again_after_cooldown_elapses(tmp_path, monkeypatch):
    decay_state = tmp_path / "drift_decay_state.json"
    drift_history = tmp_path / "drift_history.json"
    detector = _detector_with_forced_wr_drift(monkeypatch, decay_state, drift_history)

    decay_calls = []
    monkeypatch.setattr(detector, "_apply_confidence_decay", lambda: decay_calls.append(1))

    detector.check_and_respond()
    detector.check_and_respond()
    assert decay_calls == [1]

    # Simulate 25h having passed since the last decay.
    state = json.loads(decay_state.read_text())
    state["last_decay_at"] = (datetime.now() - timedelta(hours=25)).isoformat()
    decay_state.write_text(json.dumps(state))

    detector.check_and_respond()
    assert decay_calls == [1, 1]
