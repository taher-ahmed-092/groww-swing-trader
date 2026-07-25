"""Adaptive judge thresholds — the feedback loop from forced trades to the pipeline."""
from __future__ import annotations

import json

from src.memory.adaptive_thresholds import AdaptiveThresholds


def _write_history(tmp_path, monkeypatch, trades):
    # Audit bug: log_adaptation() (called at the end of update_from_all_trades())
    # writes to the real, relative data/cache/adaptation_log.json — every test
    # in this file that calls update_from_all_trades() without chdir'ing into
    # tmp_path was polluting the ACTUAL repo's adaptation log with synthetic
    # fixture data (round win rates, "200 trades" etc), which the dashboard then
    # displayed as if it were a genuine self-adjustment. chdir isolates it.
    monkeypatch.chdir(tmp_path)
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
    changes = at.update_from_all_trades()
    assert "RECOVERY" in changes
    assert changes["RECOVERY"]["new"] < changes["RECOVERY"]["old"]


def test_threshold_raises_on_low_wr(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.memory.adaptive_thresholds.THRESHOLDS_FILE", tmp_path / "th.json")
    trades = [{"regime": "RECOVERY", "outcome": "LOSS"} for _ in range(16)] + \
        [{"regime": "RECOVERY", "outcome": "WIN"} for _ in range(4)]
    _write_history(tmp_path, monkeypatch, trades)
    at = AdaptiveThresholds()
    changes = at.update_from_all_trades()
    assert "RECOVERY" in changes
    assert changes["RECOVERY"]["new"] > changes["RECOVERY"]["old"]


def test_threshold_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.memory.adaptive_thresholds.THRESHOLDS_FILE", tmp_path / "th.json")
    trades = [{"regime": "RECOVERY", "outcome": "LOSS"} for _ in range(100)]
    _write_history(tmp_path, monkeypatch, trades)
    at = AdaptiveThresholds()
    at.update_from_all_trades()
    thresholds = at.load()
    assert 5.0 <= thresholds["RECOVERY"]["judge_min"] <= 9.0


def test_get_judge_threshold_unknown_regime(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.memory.adaptive_thresholds.THRESHOLDS_FILE", tmp_path / "missing.json")
    at = AdaptiveThresholds()
    assert at.get_judge_threshold("SOME_UNKNOWN_REGIME") == 6.5


def test_default_returns_6_5(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.memory.adaptive_thresholds.THRESHOLDS_FILE", tmp_path / "missing.json")
    at = AdaptiveThresholds()
    assert at.get_judge_threshold("BULL_TRENDING") == 6.5


def test_bounded_min(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.memory.adaptive_thresholds.THRESHOLDS_FILE", tmp_path / "th.json")
    trades = [{"regime": "RECOVERY", "outcome": "WIN"} for _ in range(200)]
    _write_history(tmp_path, monkeypatch, trades)
    at = AdaptiveThresholds()
    at.update_from_all_trades()
    assert at.load()["RECOVERY"]["judge_min"] >= 5.0


def test_bounded_max(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.memory.adaptive_thresholds.THRESHOLDS_FILE", tmp_path / "th.json")
    trades = [{"regime": "BEAR_TRENDING", "outcome": "LOSS"} for _ in range(200)]
    _write_history(tmp_path, monkeypatch, trades)
    at = AdaptiveThresholds()
    at.update_from_all_trades()
    assert at.load()["BEAR_TRENDING"]["judge_min"] <= 9.0


def test_unknown_regime_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.memory.adaptive_thresholds.THRESHOLDS_FILE", tmp_path / "missing.json")
    at = AdaptiveThresholds()
    assert at.get_judge_threshold("NOT_A_REAL_REGIME") == 6.5


def test_needs_15_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.memory.adaptive_thresholds.THRESHOLDS_FILE", tmp_path / "th.json")
    trades = [{"regime": "RECOVERY", "outcome": "WIN"} for _ in range(10)]
    _write_history(tmp_path, monkeypatch, trades)
    at = AdaptiveThresholds()
    changes = at.update_from_all_trades()
    assert changes == {}


def test_threshold_write_impossible_without_evidence(tmp_path, monkeypatch):
    """A threshold change (and its adaptation-log entry) must be structurally
    impossible below MIN_EVIDENCE — regression for the phantom "BEAR_TRENDING
    8.0 -> 9.0 with 0 real evidence" bug (root cause: an unrelated test file
    leaking synthetic fixture data into the real adaptation log)."""
    from src.analytics.strategy_scorecard import ADAPTATION_LOG_FILE

    monkeypatch.setattr(
        "src.memory.adaptive_thresholds.THRESHOLDS_FILE", tmp_path / "th.json")
    trades = [{"regime": "BEAR_TRENDING", "outcome": "LOSS"} for _ in range(14)]
    _write_history(tmp_path, monkeypatch, trades)
    at = AdaptiveThresholds()
    changes = at.update_from_all_trades()
    assert changes == {}
    assert not ADAPTATION_LOG_FILE.exists()


def test_non_market_regime_tag_ignored(tmp_path, monkeypatch):
    """continuous_simulator.py tags trades with stock-level "UPTREND"/
    "DOWNTREND", not a market regime — those must never bucket into
    threshold evidence (they aren't in DEFAULT_THRESHOLDS)."""
    monkeypatch.setattr(
        "src.memory.adaptive_thresholds.THRESHOLDS_FILE", tmp_path / "th.json")
    trades = [{"regime": "DOWNTREND", "outcome": "LOSS"} for _ in range(30)]
    _write_history(tmp_path, monkeypatch, trades)
    at = AdaptiveThresholds()
    changes = at.update_from_all_trades()
    assert changes == {}
    assert at.load().get("BEAR_TRENDING", {}).get("judge_min", 8.0) == 8.0
