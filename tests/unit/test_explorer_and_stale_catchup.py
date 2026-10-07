"""EXPLORER control engine, stale catch-up exclusion, and filter-value analytics."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from sqlmodel import Session

import src.analytics.filter_value as fv
import src.trading.explorer as ex
from src.analytics import era
from src.analytics.strategy_scorecard import EngineScorecard
from src.memory.journal import CLOSE_REASON_STALE_CATCHUP, TradeRecord, TradingJournal
from src.risk.checker import _todays_real_trades

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def explorer(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "EXPLORER_OPEN_FILE", tmp_path / "open.json")
    monkeypatch.setattr(ex, "EXPLORER_HISTORY_FILE", tmp_path / "hist.json")
    monkeypatch.setattr(fv, "EXPLORER_HISTORY_FILE", tmp_path / "hist.json")
    monkeypatch.setattr("src.memory.company_dossier.dossier_store", MagicMock())
    monkeypatch.setattr(ex.AlwaysOnTrader, "_is_market_hours", staticmethod(lambda now: True))
    monkeypatch.setattr(ex.AlwaysOnTrader, "_current_regime", staticmethod(lambda: "RANGE_BOUND"))
    monkeypatch.setattr(ex, "compute_indicators",
                        lambda df: {"rsi_14": 55, "adx_signal": "CHOPPY",
                                    "supertrend_direction": "BULLISH"})
    trader = ex.ExplorerTrader()
    trader.fetcher = MagicMock()
    trader.fetcher.get_price_history.return_value = pd.DataFrame({"Close": [100.0] * 40})
    return trader


def _make_closed_today(journal, symbol, close_reason):
    with Session(journal.engine) as session:
        rec = TradeRecord(symbol=symbol, entry_price=100.0, outcome="OPEN")
        session.add(rec)
        session.commit()
        trade_id = rec.id
    return journal.log_closed(trade_id, 90.0, close_reason=close_reason)


def test_stale_catchup_close_is_excluded_from_daily_loss_sum(tmp_path):
    journal = TradingJournal(db_path=str(tmp_path / "t.db"))
    _make_closed_today(journal, "TANLA", CLOSE_REASON_STALE_CATCHUP)
    normal = _make_closed_today(journal, "OFSS", None)

    assert [t.id for t in _todays_real_trades(journal)] == [normal.id]


def test_explorer_trades_never_touch_trade_record(explorer, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("EXPLORER must not open the TradeRecord journal")

    monkeypatch.setattr("src.memory.journal.TradingJournal.__init__", forbidden)

    placed = explorer.place_entries()
    assert len(placed) == explorer.ENTRIES_PER_CALL
    t = placed[0]
    assert t["trade_type"] == "EXPLORER" and t["entry_snapshot"] and t["filters"]["rsi_zone"]
    assert t["stop"] == pytest.approx(93.0) and t["target"] == pytest.approx(114.0)

    explorer.fetcher.get_current_price.return_value = 90.0  # below every stop
    closed = explorer.close_open_trades()
    assert len(closed) == len(placed) and all(c["outcome"] == "LOSS" for c in closed)
    assert json.loads(ex.EXPLORER_HISTORY_FILE.read_text())[0]["engine"] == "EXPLORER"


def test_explorer_respects_open_cap_without_other_gates(explorer):
    ex.EXPLORER_OPEN_FILE.write_text(json.dumps(
        [{"symbol": f"S{i}", "opened_at": datetime.now(IST).isoformat()}
         for i in range(explorer.MAX_OPEN)]))
    assert explorer.place_entries() == []
    assert explorer.last_block_reason == "cap"


def test_explorer_is_scored_but_never_throttled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(era, "ERA_MARKER_FILE", tmp_path / "era_marker.json")
    p = tmp_path / "data/cache/explorer_history.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps([{"outcome": "LOSS", "pnl_pct": -5.0}] * 60))

    card = EngineScorecard()
    assert card.compute()["EXPLORER"]["n_trades"] == 60
    card.apply_throttles()
    assert EngineScorecard.get_mode("EXPLORER") == "normal"


def test_filter_value_splits_with_and_without_gate():
    def trade(rsi_ok, outcome, pnl):
        return {"outcome": outcome, "pnl_pct": pnl,
                "filters": {"rsi_zone": rsi_ok, "adx_trending": True, "supertrend": True,
                            "tier": "large", "regime": "BULL_TRENDING"}}

    value = fv.explorer_filter_value([trade(True, "WIN", 4.0), trade(False, "LOSS", -3.0)])
    assert value["binary"]["rsi_zone"]["pass"]["win_rate"] == 1.0
    assert value["binary"]["rsi_zone"]["block"]["win_rate"] == 0.0
    assert value["tier"]["large"]["n_trades"] == 2


def test_rejection_outcomes_group_by_reason_and_skip_forced_rows():
    def sim(reason, won):
        return SimpleNamespace(rejection_reason=reason, would_have_won=won, outcome_7d=2.0)

    out = fv.rejection_reason_value([
        sim("POOR_RISK_REWARD: net R:R 1.2", True), sim("POOR_RISK_REWARD", False),
        sim("FORCED_LEARNING: x", True), sim("fundamental score 0.3 below min", None)])
    assert out == {"POOR_RISK_REWARD": {"n": 2, "would_win_rate": 0.5, "avg_7d_pct": 2.0}}


def test_kill_switch_halts_explorer(explorer, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "KILL_SWITCH").write_text("")

    assert explorer.place_entries() == []
    assert explorer.last_block_reason == "kill_switch"
    explorer.fetcher.get_price_history.assert_not_called()
