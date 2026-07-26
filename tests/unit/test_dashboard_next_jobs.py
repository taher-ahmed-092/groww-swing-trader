"""_next_jobs — cross-day lookahead so weekends don't show "None today"
(audit finding)."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from dashboard.server import _next_jobs

IST = ZoneInfo("Asia/Kolkata")


def test_next_job_same_day():
    now = datetime(2026, 7, 27, 8, 0, tzinfo=IST)  # Monday 8am, before first job
    jobs = _next_jobs(now)
    assert jobs[0]["day"] is None
    assert jobs[0]["time"] == "09:00"


def test_next_job_rolls_to_next_weekday_after_last_job():
    now = datetime(2026, 7, 27, 23, 0, tzinfo=IST)  # Monday 11pm, all jobs done
    jobs = _next_jobs(now)
    assert jobs
    assert jobs[0]["day"] == "Tue"
    assert jobs[0]["time"] == "09:00"


def test_next_job_rolls_over_weekend():
    now = datetime(2026, 7, 31, 23, 0, tzinfo=IST)  # Friday 11pm
    jobs = _next_jobs(now)
    assert jobs
    assert jobs[0]["day"] == "Mon"
    assert jobs[0]["time"] == "09:00"


def test_next_job_never_empty_on_weekend():
    now = datetime(2026, 8, 1, 12, 0, tzinfo=IST)  # Saturday noon
    jobs = _next_jobs(now)
    assert len(jobs) > 0


def test_fii_feed_failure_marks_unavailable_not_zero(tmp_path, monkeypatch):
    """A fetch failure must render as {"unavailable": True}, not a bare {}
    that the frontend renders as a false "+0Cr" (audit finding)."""
    monkeypatch.chdir(tmp_path)
    import src.data.realtime_feeds as feeds
    from dashboard.server import collect_dashboard_data

    def _raise(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(feeds.FIIDIIFeed, "get_latest", _raise)
    data = collect_dashboard_data()
    assert data["fii_dii"] == {"unavailable": True}
