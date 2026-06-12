"""Trading time window + executor window enforcement (live-only auto-cancel)."""
from __future__ import annotations

from src.agents.executor.agent import ExecutorAgent
from src.broker.paper import PaperBroker
from src.memory.journal import TradingJournal
from src.trading.time_window import TradingTimeWindow


def test_strong_trend_gets_morning_window():
    tw = TradingTimeWindow().compute_entry_window(
        {"signal": "BUY", "indicators": {"rsi_14": 58, "adx_signal": "TRENDING"}}, {}
    )
    assert tw["window_label"] == "MORNING_MOMENTUM"


def test_moderate_setup_gets_flexible():
    tw = TradingTimeWindow().compute_entry_window(
        {"signal": "BUY", "indicators": {"rsi_14": 52, "adx_signal": "NEUTRAL"}}, {}
    )
    assert tw["window_label"] == "FLEXIBLE_ENTRY"


def _approved_state():
    return {
        "symbol": "TESTCO",
        "risk_check": {"approved": True},
        "trade_decision": {"symbol": "TESTCO", "order_type": "BUY", "quantity": 1,
                           "price": 100.0, "stop_price": 93.0, "target_price": 114.0},
        "time_window": {"current_status": "MISSED", "countdown_display": "closed"},
        "technical_verdict": {}, "fundamental_verdict": {}, "judge_verdict": {},
    }


def test_paper_mode_always_executes(tmp_path, monkeypatch):
    # Paper/demo must NOT auto-cancel on a missed window (Phase 1 unblock intent).
    monkeypatch.setattr("src.agents.executor.agent.settings.live_trading_enabled", False)
    ex = ExecutorAgent()
    ex.journal = TradingJournal(db_path=str(tmp_path / "tw.db"))
    result = ex.execute(_approved_state())
    assert result["status"] == "PAPER_FILLED"


def test_missed_window_cancels_live_only(tmp_path, monkeypatch):
    # In live mode a missed window auto-cancels. (Broker patched to paper to avoid
    # needing real Groww credentials — we're testing the window gate, not the broker.)
    monkeypatch.setattr("src.agents.executor.agent.settings.live_trading_enabled", True)
    ex = ExecutorAgent()
    ex.journal = TradingJournal(db_path=str(tmp_path / "tw.db"))
    monkeypatch.setattr(ex, "_select_broker", lambda: PaperBroker())
    result = ex.execute(_approved_state())
    assert result["status"] == "CANCELLED_WINDOW_MISSED"
