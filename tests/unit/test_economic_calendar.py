"""Economic calendar — size reduction ahead of high-impact events."""
from __future__ import annotations

from src.data.realtime_feeds import EconomicCalendar


def test_should_reduce_when_event_close(monkeypatch):
    cal = EconomicCalendar()
    monkeypatch.setattr(cal, "get_upcoming",
                        lambda days_ahead=7: [{"event": "RBI MPC Decision", "impact": "HIGH", "days_away": 1}])
    should_reduce, reason = cal.should_reduce_size()
    assert should_reduce is True
    assert "RBI" in reason


def test_no_reduce_no_events(monkeypatch):
    cal = EconomicCalendar()
    monkeypatch.setattr(cal, "get_upcoming", lambda days_ahead=7: [])
    should_reduce, reason = cal.should_reduce_size()
    assert should_reduce is False
    assert reason == ""
