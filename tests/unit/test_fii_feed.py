"""FII/DII feed — a failed/weekend fetch must never render as an indistinguishable
fake "+0Cr"; it should show the last known value with its date, or "unavailable"
if nothing has ever been fetched."""
from __future__ import annotations

from unittest.mock import patch

from src.data.realtime_feeds import FIIDIIFeed


def test_unavailable_when_never_fetched(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch.object(FIIDIIFeed, "_fetch_today", return_value={}):
        result = FIIDIIFeed().get_latest()
    assert result == {"unavailable": True}


def test_falls_back_to_last_known_with_stale_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    feed = FIIDIIFeed()
    with patch.object(FIIDIIFeed, "_fetch_today",
                       return_value={"fii_net_crore": 1234.0, "signal": "BULLISH"}):
        first = feed.get_latest()
    assert first["stale"] is False
    assert first["fii_net_crore"] == 1234.0

    with patch.object(FIIDIIFeed, "_fetch_today", return_value={}):
        second = FIIDIIFeed().get_latest()
    assert second["stale"] is True
    assert second["fii_net_crore"] == 1234.0
    assert "as_of" in second


def test_fresh_fetch_marked_not_stale(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch.object(FIIDIIFeed, "_fetch_today",
                       return_value={"fii_net_crore": -600.0, "signal": "BEARISH"}):
        result = FIIDIIFeed().get_latest()
    assert result["stale"] is False
    assert "as_of" in result
