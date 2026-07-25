"""Forced trades are now multi-day positions (audit fix: 90-min exit vs 6%+
targets inverted realized R:R). Verifies stop/target/time-exit close logic."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from src.trading.always_on_trader import FORCED_TRADE_FILE, IST, AlwaysOnTrader


def _open_trade(days_ago: float, entry=100.0, stop=93.0, target=110.0):
    opened_at = (datetime.now(IST) - timedelta(days=days_ago)).isoformat()
    return {
        "symbol": "TESTCO", "entry": entry, "stop": stop, "target": target,
        "exit": None, "pnl_pct": None, "outcome": "OPEN", "signal_score": 0.5,
        "opened_at": opened_at, "closed_at": None, "trade_type": "LIVE_FORCED",
        "rationale": "test", "is_forced": True, "market_was_closed": False, "tier": "large",
    }


def _seed_open(trades: list) -> None:
    FORCED_TRADE_FILE.parent.mkdir(parents=True, exist_ok=True)
    FORCED_TRADE_FILE.write_text(json.dumps(trades))


def test_stays_open_between_stop_and_target(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_open([_open_trade(2)])
    trader = AlwaysOnTrader()
    monkeypatch.setattr(trader, "_current_price", lambda symbol: 100.0)
    closed = trader.close_open_forced_trades()
    assert closed == []
    assert len(json.loads(FORCED_TRADE_FILE.read_text())) == 1


def test_closes_loss_on_stop_hit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_open([_open_trade(2)])
    trader = AlwaysOnTrader()
    monkeypatch.setattr(trader, "_current_price", lambda symbol: 90.0)  # below stop=93
    closed = trader.close_open_forced_trades()
    assert len(closed) == 1
    assert closed[0]["outcome"] == "LOSS"
    assert json.loads(FORCED_TRADE_FILE.read_text()) == []


def test_closes_win_on_target_hit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_open([_open_trade(1)])
    trader = AlwaysOnTrader()
    monkeypatch.setattr(trader, "_current_price", lambda symbol: 115.0)  # above target=110
    closed = trader.close_open_forced_trades()
    assert len(closed) == 1
    assert closed[0]["outcome"] == "WIN"


def test_time_exit_after_max_hold_days(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_open([_open_trade(AlwaysOnTrader.MAX_HOLD_DAYS + 1)])
    trader = AlwaysOnTrader()
    monkeypatch.setattr(trader, "_current_price", lambda symbol: 102.0)  # between stop/target
    closed = trader.close_open_forced_trades()
    assert len(closed) == 1  # forced closed by time, even though price is neutral


def test_within_hold_window_not_forced_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_open([_open_trade(AlwaysOnTrader.MAX_HOLD_DAYS - 1)])
    trader = AlwaysOnTrader()
    monkeypatch.setattr(trader, "_current_price", lambda symbol: 102.0)
    closed = trader.close_open_forced_trades()
    assert closed == []


def test_max_open_forced_cap_blocks_new_entries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_open([_open_trade(0, entry=100.0 + i) for i in range(AlwaysOnTrader.MAX_OPEN_FORCED)])
    trader = AlwaysOnTrader()
    result = trader.ensure_daily_trades()
    assert result == []
