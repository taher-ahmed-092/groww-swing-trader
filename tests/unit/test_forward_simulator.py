"""Forward simulator — logging and outcome filling (fetcher stubbed)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.learning.forward_simulator import ForwardSimulator
from src.memory.journal import TradingJournal


def _state(symbol="RELIANCE"):
    return {
        "symbol": symbol,
        "technical_verdict": {"signal": "BUY", "strategy_name": "momentum",
                              "entry_price": 100.0, "stop_price": 93.0,
                              "target_price": 114.0, "score": 0.7},
        "fundamental_verdict": {"score": 0.6},
        "judge_verdict": {"overall_score": 7.0},
        "market_context": {"regime": "RANGE_BOUND"},
    }


def test_log_candidate_saves(tmp_path):
    journal = TradingJournal(db_path=str(tmp_path / "sim.db"))
    ForwardSimulator(journal).log_candidate(_state(), decision="REJECTED",
                                            rejection_reason="DOWNTREND")
    sims = journal.get_simulations()
    assert len(sims) == 1
    assert sims[0].symbol == "RELIANCE"
    assert sims[0].regime == "RANGE_BOUND"


def test_fill_outcomes_7d(tmp_path, monkeypatch):
    journal = TradingJournal(db_path=str(tmp_path / "sim.db"))
    # Seed a simulation aged 8 days.
    journal.log_simulated_trade(
        symbol="RELIANCE", signal="BUY", strategy_name="momentum", regime="RANGE_BOUND",
        entry_price=100.0, stop_price=93.0, target_price=114.0,
        simulated_at=datetime.now(timezone.utc) - timedelta(days=8),
    )
    sim = ForwardSimulator(journal)
    monkeypatch.setattr(sim.fetcher, "get_current_price", lambda s: 116.0)  # hit target
    result = sim.fill_simulation_outcomes()
    assert result["updated"] == 1
    filled = journal.get_simulations()[0]
    assert filled.outcome_7d is not None
    assert filled.would_have_won is True


def test_insights_require_10_trades(tmp_path):
    journal = TradingJournal(db_path=str(tmp_path / "sim.db"))
    ForwardSimulator(journal).log_candidate(_state(), rejection_reason="LOW")
    insights = ForwardSimulator(journal).get_simulation_insights()
    assert insights["status"] == "not_enough_data"
