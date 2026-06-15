"""Daily trade guarantee — counting + graceful discovery."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session

from src.memory.journal import TradeRecord, TradingJournal
from src.trading.daily_guarantee import DailyTradeGuarantee


def _tmp_guarantee(tmp_path) -> DailyTradeGuarantee:
    g = DailyTradeGuarantee()
    g.journal = TradingJournal(db_path=str(tmp_path / "t.db"))
    return g


def test_no_trades_today(tmp_path):
    g = _tmp_guarantee(tmp_path)
    assert g.get_todays_trade_count() == 0


def test_has_trades_today(tmp_path):
    g = _tmp_guarantee(tmp_path)
    with Session(g.journal.engine) as s:
        s.add(TradeRecord(symbol="X", executed_at=datetime.now(timezone.utc)))
        s.commit()
    assert g.get_todays_trade_count() >= 1


def test_find_best_available_no_crash(monkeypatch):
    # No data from any source → returns None gracefully, never raises.
    monkeypatch.setattr("src.data.regime_detector.RegimeDetector.detect",
                        lambda self: {"regime": "UNKNOWN"})
    monkeypatch.setattr(
        "src.strategies.pairs_trading.PairsTradingStrategy.find_opportunities",
        lambda self: [])
    monkeypatch.setattr("src.data.fetcher.MarketDataFetcher.get_price_history",
                        lambda self, *a, **k: None)
    assert DailyTradeGuarantee().find_best_available_trade() is None
