"""Real-time feeds — never crash on network errors or outside their window."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from src.data.realtime_feeds import FIIDIIFeed, NSEPreOpenFeed

IST = ZoneInfo("Asia/Kolkata")


def test_fii_dii_no_crash_on_network_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.data.realtime_feeds.CACHE_DIR", tmp_path / "cache")
    with patch("requests.Session") as mock_session:
        mock_session.return_value.__enter__.return_value.get.side_effect = Exception("network down")
        result = FIIDIIFeed().get_latest()
    assert result == {}


def test_preopen_outside_window(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.data.realtime_feeds.CACHE_DIR", tmp_path / "cache")
    fake_now = datetime(2026, 1, 1, 15, 0, tzinfo=IST)
    with patch("src.data.realtime_feeds.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        result = NSEPreOpenFeed().get_preopen_data()
    assert result == {}
