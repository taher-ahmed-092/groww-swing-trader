"""Always-on forced trader — daily targeting, stop-loss, outcomes, learning."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

import src.trading.always_on_trader as aot
from src.memory.journal import TradingJournal
from src.trading.always_on_trader import AlwaysOnTrader

IST = ZoneInfo("Asia/Kolkata")


def _tmp_files(tmp_path, monkeypatch):
    monkeypatch.setattr(aot, "FORCED_TRADE_FILE", tmp_path / "open.json")
    monkeypatch.setattr(aot, "FORCED_HISTORY_FILE", tmp_path / "hist.json")
    # Isolate from real data/cache/engine_throttle.json — these tests exercise
    # daily-count/cap logic, not the meta-learning throttle, which reads real
    # repo state by design (so it works across process restarts).
    from src.analytics.strategy_scorecard import EngineScorecard

    monkeypatch.setattr(EngineScorecard, "get_mode", staticmethod(lambda engine: "normal"))


def _today_iso() -> str:
    return datetime.now(IST).isoformat()


def test_places_trades_when_below_target(tmp_path, monkeypatch):
    _tmp_files(tmp_path, monkeypatch)
    trader = AlwaysOnTrader()
    monkeypatch.setattr(trader, "_is_market_hours", lambda now: True)
    monkeypatch.setattr(trader, "_place_live_forced_trade",
                        lambda: {"symbol": "TEST", "outcome": "OPEN",
                                 "opened_at": _today_iso(), "entry": 100.0,
                                 "stop": 95.0, "target": 110.0, "signal_score": 0.4})
    trades = trader.ensure_daily_trades()
    assert len(trades) == 5


def test_stops_when_max_reached(tmp_path, monkeypatch):
    _tmp_files(tmp_path, monkeypatch)
    trader = AlwaysOnTrader()
    # MAX_DAILY_TRADES already logged today → nothing more needed, no upper-bound spam.
    rows = ",".join('{"opened_at": "%s", "outcome": "WIN"}' % _today_iso()
                    for _ in range(trader.MAX_DAILY_TRADES))
    (tmp_path / "hist.json").write_text(f"[{rows}]")
    assert trader.ensure_daily_trades() == []


def test_keeps_placing_past_old_target(tmp_path, monkeypatch):
    _tmp_files(tmp_path, monkeypatch)
    trader = AlwaysOnTrader()
    monkeypatch.setattr(trader, "_is_market_hours", lambda now: True)
    monkeypatch.setattr(trader, "_place_live_forced_trade",
                        lambda: {"symbol": "TEST", "outcome": "OPEN",
                                 "opened_at": _today_iso(), "entry": 100.0,
                                 "stop": 95.0, "target": 110.0, "signal_score": 0.4})
    # 3 already logged (the old TARGET_DAILY_TRADES) — the new unlimited engine
    # must keep placing more, not stop at 3.
    rows = ",".join('{"opened_at": "%s", "outcome": "WIN"}' % _today_iso() for _ in range(3))
    (tmp_path / "hist.json").write_text(f"[{rows}]")
    trades = trader.ensure_daily_trades()
    assert len(trades) == 5


def test_stop_loss_always_set():
    trader = AlwaysOnTrader()
    # Normal ATR.
    t = trader._build_trade_record("X", 100.0, {"atr_14": 2.0}, 0.5, "r", "LIVE_FORCED")
    assert t["stop"] < t["entry"]
    # Huge ATR must still respect the 7% floor (stop never below entry*0.93).
    t2 = trader._build_trade_record("X", 100.0, {"atr_14": 50.0}, 0.5, "r", "LIVE_FORCED")
    assert t2["stop"] >= round(100.0 * 0.93, 2)
    assert t2["stop"] < t2["entry"]


def test_forced_flag_in_trade():
    trader = AlwaysOnTrader()
    t = trader._build_trade_record("X", 100.0, {}, 0.0, "r", "RANDOM_FORCED")
    assert t["is_forced"] is True


def test_historical_sim_outside_hours(monkeypatch):
    closes = [100 + i * 0.1 for i in range(30)]
    df = pd.DataFrame({"Open": closes, "High": [c + 1 for c in closes],
                       "Low": [c - 1 for c in closes], "Close": closes,
                       "Volume": [100000] * 30})
    trader = AlwaysOnTrader()
    monkeypatch.setattr(trader, "_is_market_hours", lambda now: False)
    monkeypatch.setattr(trader.fetcher, "get_price_history", lambda *a, **k: df)
    t = trader._place_historical_simulation_trade()
    assert t is not None
    assert t["outcome"] in ("WIN", "LOSS")
    assert t["stop"] < t["entry"]
    assert t["is_forced"] is True


def test_win_detection(monkeypatch):
    trader = AlwaysOnTrader()
    monkeypatch.setattr(trader.fetcher, "get_current_price", lambda *a, **k: 115.0)
    trade = {"symbol": "X", "entry": 100.0, "stop": 90.0, "target": 110.0}
    out = trader._close_forced_trade(trade)
    assert out["outcome"] == "WIN" and out["exit"] == 110.0


def test_loss_detection(monkeypatch):
    trader = AlwaysOnTrader()
    monkeypatch.setattr(trader.fetcher, "get_current_price", lambda *a, **k: 88.0)
    trade = {"symbol": "X", "entry": 100.0, "stop": 90.0, "target": 110.0}
    out = trader._close_forced_trade(trade)
    assert out["outcome"] == "LOSS" and out["exit"] == 90.0


def test_historical_sim_retired_off_hours(tmp_path, monkeypatch):
    """HISTORICAL_SIM is retired — off hours, ensure_daily_trades() must place
    nothing (ContinuousSimulator covers off-hours learning now)."""
    _tmp_files(tmp_path, monkeypatch)
    trader = AlwaysOnTrader()
    monkeypatch.setattr(trader, "_is_market_hours", lambda now: False)
    monkeypatch.setattr(
        trader, "_place_historical_simulation_trade",
        lambda: (_ for _ in ()).throw(AssertionError("must not be called")))
    assert trader.ensure_daily_trades() == []


def test_knowledge_update_on_close(tmp_path):
    trader = AlwaysOnTrader()
    trader.journal = TradingJournal(db_path=str(tmp_path / "kb.db"))
    trader._update_knowledge_from_trade({
        "outcome": "WIN", "symbol": "X", "signal_score": 0.5,
        "trade_type": "LIVE_FORCED", "pnl_pct": 3.0})
    kb = trader.journal.get_active_knowledge(min_confidence=0.0)
    assert any(e.category == "FORCED_LEARNING" for e in kb)
