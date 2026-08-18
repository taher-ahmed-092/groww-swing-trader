"""Regression tests for the 2026-08 laptop-off-11-days incident:
- /report's "Real Trades" and "What to Improve" sections used two independent
  paths to count real trades — one saw open positions, the other didn't, so
  "4 open, 0 closed" and "0 so far" could both appear in the same report.
- Open real positions could sit unmonitored indefinitely across a process
  outage; find_stale_open_positions() flags any past STALE_POSITION_MAX_HOLD_DAYS
  so runner.py's startup check can price/close them immediately.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from src.memory.journal import TradeRecord, TradingJournal
from src.notifications.command_handler import CommandHandler
from src.risk.checker import STALE_POSITION_MAX_HOLD_DAYS, find_stale_open_positions


def _seed_open_trade(journal: TradingJournal, symbol: str, days_old: int,
                      entry_price: float = 100.0, stop_price: float = 92.0) -> None:
    from sqlmodel import Session

    with Session(journal.engine) as session:
        session.add(TradeRecord(
            symbol=symbol, outcome="OPEN", entry_price=entry_price, stop_price=stop_price,
            executed_at=datetime.now() - timedelta(days=days_old)))
        session.commit()


def test_find_stale_open_positions_flags_old_open_trades(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    journal = TradingJournal()
    _seed_open_trade(journal, "TANLA", days_old=13)
    _seed_open_trade(journal, "INFY", days_old=1)

    stale = find_stale_open_positions(journal)

    assert len(stale) == 1
    assert stale[0]["trade"].symbol == "TANLA"
    assert stale[0]["age_days"] >= STALE_POSITION_MAX_HOLD_DAYS


def test_find_stale_open_positions_empty_when_none_stale(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    journal = TradingJournal()
    _seed_open_trade(journal, "OFSS", days_old=1)

    assert find_stale_open_positions(journal) == []


def test_improvement_tip_never_says_zero_when_positions_open():
    handler = CommandHandler.__new__(CommandHandler)
    tip = handler._generate_improvement_tip(
        summary={"total_trades": 0}, forced={}, sim_wr=0, regime="UNKNOWN",
        thresholds={}, n_open_real=4, oldest_open_days=15)

    assert "0 so far" not in tip
    assert "4 real position(s) currently open" in tip
    assert "15 day(s)" in tip


def test_improvement_tip_keeps_zero_message_when_truly_no_real_trades():
    handler = CommandHandler.__new__(CommandHandler)
    tip = handler._generate_improvement_tip(
        summary={"total_trades": 0}, forced={}, sim_wr=0, regime="UNKNOWN",
        thresholds={}, n_open_real=0, oldest_open_days=0)

    assert "0 so far" in tip
