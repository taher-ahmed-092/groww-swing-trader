"""Adaptive judge thresholds — the feedback loop from forced trades to the pipeline."""
from __future__ import annotations

import json

from src.memory.adaptive_thresholds import AdaptiveThresholds


def _write_history(tmp_path, monkeypatch, trades):
    hist = tmp_path / "forced.json"
    hist.write_text(json.dumps(trades))
    monkeypatch.setattr(
        "src.memory.adaptive_thresholds.Path",
        lambda p: hist if p == "data/cache/forced_trades_history.json" else _NullPath())


class _NullPath:
    def exists(self):
        return False


def test_default_thresholds_loaded(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.memory.adaptive_thresholds.THRESHOLDS_FILE", tmp_path / "missing.json")
    at = AdaptiveThresholds()
    thresholds = at.load()
    assert thresholds["VOLATILE"]["judge_min"] == 7.5
    assert thresholds["BULL_TRENDING"]["judge_min"] == 6.5


def test_threshold_lowers_on_high_wr(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.memory.adaptive_thresholds.THRESHOLDS_FILE", tmp_path / "th.json")
    trades = [{"regime": "RECOVERY", "outcome": "WIN"} for _ in range(16)] + \
        [{"regime": "RECOVERY", "outcome": "LOSS"} for _ in range(4)]
    _write_history(tmp_path, monkeypatch, trades)
    at = AdaptiveThresholds()
    changes = at.update_from_forced_trades()
    assert "RECOVERY" in changes
    assert changes["RECOVERY"]["new"] < changes["RECOVERY"]["old"]


def test_threshold_raises_on_low_wr(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.memory.adaptive_thresholds.THRESHOLDS_FILE", tmp_path / "th.json")
    trades = [{"regime": "RECOVERY", "outcome": "LOSS"} for _ in range(16)] + \
        [{"regime": "RECOVERY", "outcome": "WIN"} for _ in range(4)]
    _write_history(tmp_path, monkeypatch, trades)
    at = AdaptiveThresholds()
    changes = at.update_from_forced_trades()
    assert "RECOVERY" in changes
    assert changes["RECOVERY"]["new"] > changes["RECOVERY"]["old"]


def test_threshold_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.memory.adaptive_thresholds.THRESHOLDS_FILE", tmp_path / "th.json")
    trades = [{"regime": "RECOVERY", "outcome": "LOSS"} for _ in range(100)]
    _write_history(tmp_path, monkeypatch, trades)
    at = AdaptiveThresholds()
    at.update_from_forced_trades()
    thresholds = at.load()
    assert 5.0 <= thresholds["RECOVERY"]["judge_min"] <= 9.0


def test_get_judge_threshold_unknown_regime(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.memory.adaptive_thresholds.THRESHOLDS_FILE", tmp_path / "missing.json")
    at = AdaptiveThresholds()
    assert at.get_judge_threshold("SOME_UNKNOWN_REGIME") == 6.5
