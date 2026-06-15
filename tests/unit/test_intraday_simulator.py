"""Intraday simulator — outcome detection + graceful no-data behavior."""
from __future__ import annotations

import json

import src.learning.intraday_simulator as sim_mod
from src.learning.intraday_simulator import IntradaySimulator

_POS = {
    "symbol": "RELIANCE", "entry": 100.0, "stop": 93.0, "target": 108.0,
    "entry_time": "2026-06-15T09:30:00+05:30", "regime": "BULL_TRENDING",
    "rsi_at_entry": 55.0, "trend_at_entry": "UPTREND", "adx_at_entry": "STRONG",
}


def _run_exit(monkeypatch, tmp_path, current):
    """Set up one open position, force the close price, run the exit job."""
    open_file = tmp_path / "open.json"
    hist_file = tmp_path / "hist.json"
    open_file.write_text(json.dumps([dict(_POS)]))
    monkeypatch.setattr(sim_mod, "_OPEN_FILE", open_file)
    monkeypatch.setattr(sim_mod, "_HISTORY_FILE", hist_file)
    # Isolate from the real journal — outcome logic is what's under test.
    monkeypatch.setattr(IntradaySimulator, "_learn", lambda self, j, p, o: None)
    monkeypatch.setattr(sim_mod.MarketDataFetcher, "get_current_price",
                        lambda self, symbol: current)
    return IntradaySimulator().run_afternoon_exits()


def test_get_summary_no_file(monkeypatch, tmp_path):
    monkeypatch.setattr(sim_mod, "_HISTORY_FILE", tmp_path / "none.json")
    s = IntradaySimulator().get_summary()
    assert s == {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "recent": []}


def test_win_detection(monkeypatch, tmp_path):
    out = _run_exit(monkeypatch, tmp_path, current=110.0)  # >= target 108
    assert out["wins"] == 1 and out["losses"] == 0
    assert out["results"][0]["outcome"] == "WIN"
    assert out["results"][0]["exit"] == 108.0


def test_loss_detection(monkeypatch, tmp_path):
    out = _run_exit(monkeypatch, tmp_path, current=91.0)  # <= stop 93
    assert out["losses"] == 1 and out["wins"] == 0
    assert out["results"][0]["outcome"] == "LOSS"
    assert out["results"][0]["exit"] == 93.0


def test_time_exit_win(monkeypatch, tmp_path):
    out = _run_exit(monkeypatch, tmp_path, current=103.0)  # between stop/target, up
    assert out["results"][0]["outcome"] == "WIN"
    assert out["results"][0]["pnl_pct"] > 0


def test_time_exit_loss(monkeypatch, tmp_path):
    out = _run_exit(monkeypatch, tmp_path, current=97.0)  # between stop/target, down
    assert out["results"][0]["outcome"] == "LOSS"
    assert out["results"][0]["pnl_pct"] < 0


def test_morning_entries_no_market(monkeypatch):
    monkeypatch.setattr(sim_mod.NSECalendar, "is_market_open",
                        lambda self, *a, **k: False)
    assert IntradaySimulator().run_morning_entries() == []
