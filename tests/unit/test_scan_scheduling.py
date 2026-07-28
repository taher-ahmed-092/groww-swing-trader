"""Live-price pipeline scans (pairs/scan) are scheduled at 9:20/9:25 AM,
after the 9:15 AM market open — never earlier. daily_premarket_job (9:00 AM)
warns that it's running pre-open."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import runner

IST = ZoneInfo("Asia/Kolkata")


def _read_source():
    return open("runner.py", encoding="utf-8").read()


def test_pairs_job_scheduled_at_or_after_920():
    src = _read_source()
    idx = src.find('scheduler.add_job(_job(market_open_pairs_job')
    assert idx != -1
    line = src[idx: src.find("\n", idx)]
    assert "hour=9" in line and "minute=20" in line


def test_scan_job_scheduled_at_or_after_925():
    src = _read_source()
    idx = src.find('scheduler.add_job(_job(market_open_scan_job')
    assert idx != -1
    line = src[idx: src.find("\n", idx)]
    assert "hour=9" in line and "minute=25" in line


def test_premarket_job_pre_open_warning_fires_between_900_and_915(monkeypatch, capsys):
    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 28, 9, 5, tzinfo=IST)

    monkeypatch.setattr(runner, "datetime", _FrozenDatetime)
    runner._check_pre_open("daily_premarket_job")
    out = capsys.readouterr().out
    assert "pre-open run" in out
    assert "not live" in out


def test_no_pre_open_warning_after_915(monkeypatch, capsys):
    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 28, 9, 25, tzinfo=IST)

    monkeypatch.setattr(runner, "datetime", _FrozenDatetime)
    runner._check_pre_open("market_open_scan_job")
    out = capsys.readouterr().out
    assert "pre-open run" not in out
