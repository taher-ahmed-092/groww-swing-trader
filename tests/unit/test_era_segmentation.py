"""Era segmentation — pre-fix (broken 90-min engine) trades must not dilute
headline metrics computed after the multi-day-hold fix (commit f262968)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.analytics.era import ERA_MARKER_FILE, get_era_start, split_by_era

IST = ZoneInfo("Asia/Kolkata")


def test_era_marker_created_on_first_call(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not ERA_MARKER_FILE.exists()
    era_start = get_era_start()
    assert ERA_MARKER_FILE.exists()
    assert json.loads(ERA_MARKER_FILE.read_text())["era_start"] == era_start


def test_era_marker_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = get_era_start()
    second = get_era_start()
    assert first == second


def test_split_by_era_separates_pre_and_post_fix_trades(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    era_start = get_era_start()
    before = (datetime.fromisoformat(era_start) - timedelta(days=10)).isoformat()
    after = (datetime.fromisoformat(era_start) + timedelta(days=1)).isoformat()
    trades = [
        {"symbol": "OLD", "closed_at": before},
        {"symbol": "NEW", "closed_at": after},
    ]
    current_era, all_time = split_by_era(trades)
    assert len(all_time) == 2
    assert [t["symbol"] for t in current_era] == ["NEW"]


def test_split_by_era_treats_missing_timestamp_as_current(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    trades = [{"symbol": "NOTIME"}]
    current_era, all_time = split_by_era(trades)
    assert current_era == trades
    assert all_time == trades
