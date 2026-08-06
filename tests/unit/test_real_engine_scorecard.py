"""REAL engine row — added to the scorecard so the actual paper-broker fills
(OFSS/TANLA-style trades) show up alongside the JSON-cache-backed engines
instead of being invisible. Reads from TradingJournal (open + closed), not a
cache file, and is exempt from apply_throttles()'s auto-throttle/pause/retire
logic — it's real signal-quality evidence, never a disposable practice
engine."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session

from src.analytics import era
from src.analytics.strategy_scorecard import THROTTLE_FILE, EngineScorecard
from src.memory.journal import TradeRecord, TradingJournal

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture(autouse=True)
def _isolate_era_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(era, "ERA_MARKER_FILE", tmp_path / "era_marker.json")


def _seed_real_trades(journal: TradingJournal, n_wins: int, n_losses: int, n_open: int) -> None:
    # closed_at must be AFTER era_start (lazily created on first
    # get_era_start() call, stamped to "now") — a past timestamp would read
    # as pre-fix-era and get filtered out by split_by_era.
    future = datetime.now(IST) + timedelta(hours=1)
    with Session(journal.engine) as session:
        for _ in range(n_wins):
            session.add(TradeRecord(symbol="OFSS", outcome="WIN", pnl_pct=2.0, closed_at=future))
        for _ in range(n_losses):
            session.add(TradeRecord(symbol="TANLA", outcome="LOSS", pnl_pct=-3.0,
                                     entry_price=100.0, stop_price=97.0, closed_at=future))
        for _ in range(n_open):
            session.add(TradeRecord(symbol="INFY", outcome="OPEN"))
        session.commit()


def test_real_engine_appears_in_scorecard(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    journal = TradingJournal()
    _seed_real_trades(journal, n_wins=1, n_losses=1, n_open=2)

    scorecard = EngineScorecard().compute()
    assert "REAL" in scorecard
    assert scorecard["REAL"]["n_open"] == 2
    assert scorecard["REAL"]["n_trades"] == 2


def test_real_engine_never_throttled_even_at_terrible_pf(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    journal = TradingJournal()
    # Deliberately terrible PF — 1 tiny win vs 30 big losses — which would
    # pause any other engine (PF << 0.5) at PROBATION_MIN_TRADES+ evidence.
    _seed_real_trades(journal, n_wins=1, n_losses=30, n_open=0)

    changes = EngineScorecard().apply_throttles()
    assert "REAL" not in changes
    assert EngineScorecard.get_mode("REAL") == "normal"
    assert not THROTTLE_FILE.exists() or "REAL" not in (
        __import__("json").loads(THROTTLE_FILE.read_text()) if THROTTLE_FILE.exists() else {}
    )


def test_real_engine_open_trades_never_hidden_when_zero_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    journal = TradingJournal()
    _seed_real_trades(journal, n_wins=0, n_losses=0, n_open=2)

    scorecard = EngineScorecard().compute()
    assert scorecard["REAL"]["n_open"] == 2
    assert scorecard["REAL"]["n_trades"] == 0
