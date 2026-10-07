"""Demo engines report why they placed nothing and ignore the real-money loss halt."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import src.trading.always_on_trader as aot
from src.analytics.strategy_scorecard import EngineScorecard
from src.notifications.command_handler import CommandHandler
from src.trading.always_on_trader import AlwaysOnTrader


def _trader(tmp_path, monkeypatch, *, market_open=True, mode="normal"):
    monkeypatch.setattr(aot, "FORCED_TRADE_FILE", tmp_path / "open.json")
    monkeypatch.setattr(aot, "FORCED_HISTORY_FILE", tmp_path / "hist.json")
    monkeypatch.setattr(EngineScorecard, "get_mode", staticmethod(lambda engine: mode))
    trader = AlwaysOnTrader()
    monkeypatch.setattr(trader, "_is_market_hours", lambda now: market_open)
    monkeypatch.setattr(trader, "_current_regime", lambda: "RANGE_BOUND")
    return trader


@pytest.mark.parametrize("scenario, expected", [
    ("daily_cap", "daily_cap"),
    ("open_cap", "open_cap"),
    ("market_closed", "market_closed"),
    ("engine_paused", "engine_paused"),
    ("no_candidate", "no_candidate"),
])
def test_every_early_return_records_its_reason(tmp_path, monkeypatch, scenario, expected):
    trader = _trader(tmp_path, monkeypatch,
                     market_open=scenario != "market_closed",
                     mode="paused" if scenario == "engine_paused" else "normal")
    if scenario == "daily_cap":
        monkeypatch.setattr(trader, "_count_todays_forced_trades", lambda: trader.MAX_DAILY_TRADES)
    if scenario == "open_cap":
        monkeypatch.setattr(trader, "_load_open_forced_trades",
                            lambda: [{}] * trader.MAX_OPEN_FORCED)
    monkeypatch.setattr(trader, "_place_live_forced_trade", lambda: None)

    assert trader.ensure_daily_trades() == []
    assert trader.last_block_reason == expected
    assert trader.last_block_message


def test_trade_command_reports_true_reason(tmp_path, monkeypatch):
    trader = _trader(tmp_path, monkeypatch, market_open=False)
    monkeypatch.setattr(aot, "AlwaysOnTrader", lambda: trader)
    handler = CommandHandler.__new__(CommandHandler)
    handler.tg = MagicMock()
    handler.tg.chat_id = "1"
    handler.commands = {}
    CommandHandler.__init__(handler)
    sent = []
    handler._send = sent.append

    handler._force_now([])

    assert "Market is closed" in sent[0] and "market_closed" in sent[0]
    assert "Daily target" not in sent[0]


def test_real_money_halt_does_not_block_demo_engines(tmp_path, monkeypatch):
    import src.risk.checker as checker

    monkeypatch.setattr(checker, "is_daily_loss_halted", lambda: True)
    trader = _trader(tmp_path, monkeypatch)
    monkeypatch.setattr(trader, "_place_live_forced_trade",
                        lambda: {"symbol": "TEST", "outcome": "OPEN",
                                 "opened_at": "2026-01-01T10:00:00+05:30", "entry": 100.0,
                                 "stop": 93.0, "target": 114.0, "signal_score": 0.4})
    assert len(trader.ensure_daily_trades()) == 5


def test_real_money_halt_does_not_block_intraday_entries(monkeypatch):
    import runner
    import src.risk.checker as checker

    monkeypatch.setattr(checker, "is_daily_loss_halted", lambda: True)
    monkeypatch.setattr(runner, "_kill_switch", lambda: False)
    monkeypatch.setattr(runner, "_is_paused", lambda: False)
    simulator = MagicMock()
    simulator.return_value.run_morning_entries.return_value = []
    monkeypatch.setattr("src.learning.intraday_simulator.IntradaySimulator", simulator)

    runner.intraday_entry_job()

    simulator.return_value.run_morning_entries.assert_called_once()


def test_kill_switch_halts_forced_trader_and_trade_command_says_so(tmp_path, monkeypatch):
    trader = _trader(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "KILL_SWITCH").write_text("")
    monkeypatch.setattr(trader, "_place_live_forced_trade",
                        lambda: pytest.fail("must not place under KILL_SWITCH"))

    assert trader.ensure_daily_trades() == []
    assert trader.last_block_reason == "kill_switch"

    monkeypatch.setattr(aot, "AlwaysOnTrader", lambda: trader)
    handler = CommandHandler.__new__(CommandHandler)
    handler.tg = MagicMock()
    handler.tg.chat_id = "1"
    handler.commands = {}
    CommandHandler.__init__(handler)
    sent = []
    handler._send = sent.append
    handler._force_now([])
    assert "KILL_SWITCH" in sent[0] and "[kill_switch]" in sent[0]
