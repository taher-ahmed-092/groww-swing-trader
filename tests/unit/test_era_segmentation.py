"""Era segmentation — pre-fix (broken 90-min engine) trades must not dilute
headline metrics computed after the multi-day-hold fix (commit f262968)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.analytics import era

IST = ZoneInfo("Asia/Kolkata")


def _isolate(tmp_path, monkeypatch):
    """ERA_MARKER_FILE is anchored to the repo root (not cwd) so the dashboard
    and runner processes always agree on one marker — so tests must isolate it
    by monkeypatching the module attribute directly, not by chdir."""
    marker = tmp_path / "era_marker.json"
    monkeypatch.setattr(era, "ERA_MARKER_FILE", marker)
    return marker


def test_era_marker_created_on_first_call(tmp_path, monkeypatch):
    marker = _isolate(tmp_path, monkeypatch)
    assert not marker.exists()
    era_start = era.get_era_start()
    assert marker.exists()
    assert json.loads(marker.read_text())["era_start"] == era_start


def test_era_marker_never_overwritten(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    first = era.get_era_start()
    second = era.get_era_start()
    assert first == second


def test_era_marker_write_failure_falls_back_to_hardcoded_constant(tmp_path, monkeypatch):
    """If the marker can't be written (e.g. read-only filesystem), falling back
    to datetime.now() would silently redefine "current era" as "this instant"
    on every call — filtering out everything, not just genuinely pre-fix
    trades. Must fall back to the fixed f262968-commit-time constant instead."""
    unwritable = tmp_path / "no_such_dir" / "era_marker.json"
    monkeypatch.setattr(era, "ERA_MARKER_FILE", unwritable)
    monkeypatch.setattr(
        era.Path, "mkdir",
        lambda self, *a, **k: (_ for _ in ()).throw(OSError("read-only filesystem")))
    assert era.get_era_start() == era.ERA_FALLBACK_START


def test_split_by_era_separates_pre_and_post_fix_trades(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    era_start = era.get_era_start()
    before = (datetime.fromisoformat(era_start) - timedelta(days=10)).isoformat()
    after = (datetime.fromisoformat(era_start) + timedelta(days=1)).isoformat()
    trades = [
        {"symbol": "OLD", "closed_at": before},
        {"symbol": "NEW", "closed_at": after},
    ]
    current_era, all_time = era.split_by_era(trades)
    assert len(all_time) == 2
    assert [t["symbol"] for t in current_era] == ["NEW"]


def test_split_by_era_treats_missing_timestamp_as_current(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    trades = [{"symbol": "NOTIME"}]
    current_era, all_time = era.split_by_era(trades)
    assert current_era == trades
    assert all_time == trades


def test_split_by_era_honors_simulated_at(tmp_path, monkeypatch):
    """continuous_sim_history.json rows stamp "simulated_at", not
    "closed_at"/"opened_at" — a pre-fix simulated_at must be excluded from the
    current era, not default-included just because the other two keys are
    absent."""
    _isolate(tmp_path, monkeypatch)
    era_start = era.get_era_start()
    before = (datetime.fromisoformat(era_start) - timedelta(days=5)).isoformat()
    after = (datetime.fromisoformat(era_start) + timedelta(hours=1)).isoformat()
    trades = [
        {"symbol": "OLDSIM", "simulated_at": before},
        {"symbol": "NEWSIM", "simulated_at": after},
    ]
    current_era, all_time = era.split_by_era(trades)
    assert len(all_time) == 2
    assert [t["symbol"] for t in current_era] == ["NEWSIM"]
