"""Merged trade history — real + forced + intraday + short, tagged by source."""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

from dashboard.server import _load_all_trade_history


def _real_trade(symbol, pnl, outcome, closed_at):
    t = MagicMock()
    t.symbol = symbol
    t.pnl_pct = pnl
    t.outcome = outcome
    t.closed_at = closed_at
    return t


def test_merge_includes_all_sources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "cache").mkdir(parents=True)
    (tmp_path / "data" / "cache" / "forced_trades_history.json").write_text(
        json.dumps([{"symbol": "TCS", "pnl_pct": 1.0, "outcome": "WIN", "closed_at": "2026-01-02T10:00:00"}]))
    (tmp_path / "data" / "cache" / "intraday_sim_history.json").write_text(
        json.dumps([{"symbol": "INFY", "pnl_pct": -2.0, "outcome": "LOSS", "closed_at": "2026-01-03T10:00:00"}]))
    (tmp_path / "data" / "cache" / "short_trades_history.json").write_text(
        json.dumps([{"symbol": "WIPRO", "pnl_pct": 3.0, "outcome": "WIN", "closed_at": "2026-01-04T10:00:00"}]))

    journal = MagicMock()
    journal.get_recent.return_value = [
        _real_trade("RELIANCE", 5.0, "WIN", datetime(2026, 1, 1, 10, 0, 0))]

    merged = _load_all_trade_history(journal)
    sources = {m["source"] for m in merged}
    assert sources == {"real", "forced", "intraday", "short"}
    assert len(merged) == 4


def test_merge_handles_missing_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    journal = MagicMock()
    journal.get_recent.return_value = []
    assert _load_all_trade_history(journal) == []


def test_merge_sorted_by_time(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "cache").mkdir(parents=True)
    (tmp_path / "data" / "cache" / "forced_trades_history.json").write_text(
        json.dumps([
            {"symbol": "A", "pnl_pct": 1.0, "outcome": "WIN", "closed_at": "2026-01-05T10:00:00"},
            {"symbol": "B", "pnl_pct": 1.0, "outcome": "WIN", "closed_at": "2026-01-01T10:00:00"},
        ]))
    journal = MagicMock()
    journal.get_recent.return_value = []
    merged = _load_all_trade_history(journal)
    closed_ats = [m["closed_at"] for m in merged]
    assert closed_ats == sorted(closed_ats)
