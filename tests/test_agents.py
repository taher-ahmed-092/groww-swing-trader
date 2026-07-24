"""Behavior tests for the upgraded judge, screener, and lessons components."""
from __future__ import annotations

from src.data.screener import ScreenerScraper
from src.judge.evaluator import LLMJudge
from src.memory.journal import TradingJournal
from src.memory.lessons import LessonsRetriever
from src.orchestrator.state import get_initial_state


def _base_state():
    state = get_initial_state("TESTCO")
    # Pre-set market context so the judge never hits the network in tests.
    state["market_context"] = {
        "nifty_trend": "UPTREND",
        "market_safe_to_buy": True,
        "context_summary": "test UPTREND",
    }
    state["fundamental_verdict"] = {"score": 0.85, "proceed": True, "hard_rejected": False}
    state["technical_verdict"] = {
        "score": 0.85, "signal": "BUY", "proceed": True,
        "entry_price": 100.0, "stop_price": 93.0, "target_price": 114.0,
        "weekly_trend": "UPTREND", "indicators": {"adx_signal": "TRENDING"},
    }
    return state


def test_auto_veto_choppy_market(monkeypatch):
    # CHOPPY_MARKET only auto-vetoes in modes that require a trending market
    # (conserve). Balanced/rogue intentionally let the LLM scorecard weigh
    # chop instead of a hard pre-LLM rejection (src/trading/modes.py
    # require_adx_trending) -- this test exercises the conserve-mode case.
    from src.trading.modes import MODES

    monkeypatch.setattr("src.judge.evaluator.get_current_mode", lambda: MODES["conserve"])
    state = _base_state()
    state["technical_verdict"]["indicators"]["adx_signal"] = "CHOPPY"
    verdict = LLMJudge().evaluate(state)
    assert verdict["approved"] is False
    assert "CHOPPY_MARKET" in verdict["flags"]


def test_no_choppy_veto_in_balanced_mode(monkeypatch):
    from src.trading.modes import MODES

    monkeypatch.setattr("src.judge.evaluator.get_current_mode", lambda: MODES["balanced"])
    state = _base_state()
    state["technical_verdict"]["indicators"]["adx_signal"] = "CHOPPY"
    verdict = LLMJudge().evaluate(state)
    assert "CHOPPY_MARKET" not in verdict["flags"]


def test_auto_veto_fighting_nifty():
    state = _base_state()
    state["market_context"]["nifty_trend"] = "DOWNTREND"
    state["technical_verdict"]["signal"] = "BUY"
    verdict = LLMJudge().evaluate(state)
    assert verdict["approved"] is False
    assert "FIGHTING_NIFTY" in verdict["flags"]


def test_hard_reject_high_pledging():
    rejects = ScreenerScraper().check_hard_rejects({"promoter_pledged_pct": 45})
    assert len(rejects) == 1
    assert "pledging" in rejects[0].lower()


def test_lessons_retriever_empty_graceful(tmp_path):
    # Isolated, empty journal — must return "" without raising.
    journal = TradingJournal(db_path=str(tmp_path / "empty.db"))
    result = LessonsRetriever(journal=journal).get_relevant_lessons("NEWSTOCK")
    assert result == ""
