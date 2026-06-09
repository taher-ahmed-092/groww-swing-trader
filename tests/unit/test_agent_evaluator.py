"""Agent evaluation framework behavior."""
from __future__ import annotations

from src.evaluation.agent_evaluator import AgentEvaluator
from src.memory.journal import TradingJournal


def _seed(journal, fund, judge, signal, pnl_pct, symbol="TESTCO"):
    entry = 100.0
    state = {
        "symbol": symbol,
        "fundamental_verdict": {"score": fund},
        "technical_verdict": {
            "score": fund, "signal": signal, "patterns": ["HAMMER"],
            "indicators": {"rsi_14": 57}, "entry_price": entry,
            "stop_price": 93.0, "target_price": 114.0,
        },
        "judge_verdict": {"overall_score": judge},
        "market_context": {"nifty_trend": "UPTREND"},
        "sector": "IT",
    }
    rec = journal.log_proposed(state)
    journal.log_executed(rec.id, {"fill_price": entry, "broker_mode": "paper"})
    journal.log_closed(rec.id, round(entry * (1 + pnl_pct / 100), 2))


def test_insufficient_data_returns_status(tmp_path):
    j = TradingJournal(db_path=str(tmp_path / "e.db"))
    for _ in range(3):
        _seed(j, 0.8, 8.0, "BUY", 5.0)
    out = AgentEvaluator(journal=j).evaluate_fundamental_accuracy()
    assert out.get("status") == "insufficient_data"


def test_correlation_calculated_correctly(tmp_path):
    j = TradingJournal(db_path=str(tmp_path / "e.db"))
    for _ in range(3):
        _seed(j, 0.9, 8.5, "BUY", 8.0)   # high score → win
    for _ in range(3):
        _seed(j, 0.45, 6.0, "BUY", -7.0)  # low score → loss
    out = AgentEvaluator(journal=j).evaluate_fundamental_accuracy()
    assert out["score_correlation"] > 0.5


def test_calibration_error_calculated(tmp_path):
    j = TradingJournal(db_path=str(tmp_path / "e.db"))
    for _ in range(5):
        _seed(j, 0.8, 8.5, "BUY", 6.0)   # judge in 8-9 bucket, wins
    out = AgentEvaluator(journal=j).evaluate_judge_calibration()
    assert out["buckets"]["8-9"]["total"] >= 1
    assert out["avg_calibration_error"] is not None


def test_suggestions_generated():
    eval_results = {
        "fundamental": {"score_correlation": 0.05, "trades_analyzed": 10},
        "technical": {"buy_signal_win_rate": 0.4},
        "judge": {"avg_calibration_error": 0.25},
    }
    suggestions = AgentEvaluator().generate_improvement_suggestions(eval_results)
    assert len(suggestions) == 3
