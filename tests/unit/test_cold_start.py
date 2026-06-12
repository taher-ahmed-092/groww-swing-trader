"""Cold-start protection + parameter momentum guard in the workflow enhancer."""
from __future__ import annotations

import pytest
from sqlmodel import Session

from src.agents.meta.workflow_enhancer import WorkflowEnhancer
from src.memory.journal import TradeRecord, TradingJournal

_POOR = {
    "fundamental": {"score_correlation": 0.05, "trades_analyzed": 10},
    "technical": {"buy_signal_win_rate": 0.4},
    "judge": {"avg_calibration_error": 0.25},
}


def _seed(journal: TradingJournal, n: int) -> None:
    with Session(journal.engine) as s:
        for i in range(n):
            s.add(TradeRecord(symbol=f"S{i}", outcome="WIN" if i % 2 else "LOSS",
                              pnl_pct=5.0 if i % 2 else -5.0, entry_price=100.0,
                              stop_price=93.0, target_price=114.0, is_seeded=False))
        s.commit()


@pytest.fixture()
def enhancer(tmp_path, monkeypatch):
    monkeypatch.setattr("src.utils.adaptive.PARAMS_FILE", str(tmp_path / "params.json"))
    monkeypatch.setattr(WorkflowEnhancer, "MOMENTUM_FILE", tmp_path / "momentum.json")
    journal = TradingJournal(db_path=str(tmp_path / "cs.db"))
    return WorkflowEnhancer(journal=journal), journal


def test_no_suggestions_before_5_trades(enhancer):
    wf, _ = enhancer
    result = wf.run(_POOR)
    assert result["status"] == "waiting"
    assert result["auto_applied"] == []


def test_no_auto_apply_before_15_trades(enhancer):
    wf, journal = enhancer
    _seed(journal, 10)  # >= 5 (suggestions) but < 15 (auto-apply)
    wf.run(_POOR)
    result = wf.run(_POOR)
    assert result["auto_applied"] == []
    assert result["suggestions"]  # suggestions still generated


def test_momentum_guard_same_direction(enhancer):
    wf, _ = enhancer
    wf._record_suggestion("scout_rsi_low", 55)  # prior suggestion: up from 50
    assert wf._should_apply_change("scout_rsi_low", 56, current_value=50) is True


def test_momentum_guard_opposite_direction(enhancer):
    wf, _ = enhancer
    wf._record_suggestion("scout_rsi_low", 45)  # prior suggestion: down from 50
    assert wf._should_apply_change("scout_rsi_low", 55, current_value=50) is False
