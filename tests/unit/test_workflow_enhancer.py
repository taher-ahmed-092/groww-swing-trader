"""Workflow enhancer behavior (heuristic path — no API key in test env)."""
from __future__ import annotations

import json

import pytest
from sqlmodel import Session

from src.agents.meta.workflow_enhancer import WorkflowEnhancer
from src.memory.journal import TradeRecord, TradingJournal
from src.utils.adaptive import ADJUSTABLE_PARAMS

_POOR = {
    "fundamental": {"score_correlation": 0.05, "trades_analyzed": 10},
    "technical": {"buy_signal_win_rate": 0.4},
    "judge": {"avg_calibration_error": 0.25},
}


def _seed_real_trades(journal: TradingJournal, n: int) -> None:
    with Session(journal.engine) as s:
        for i in range(n):
            s.add(TradeRecord(
                symbol=f"S{i}", outcome="WIN" if i % 2 else "LOSS",
                pnl_pct=5.0 if i % 2 else -5.0, entry_price=100.0,
                stop_price=93.0, target_price=114.0, is_seeded=False,
            ))
        s.commit()


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    params_file = str(tmp_path / "adaptive_params.json")
    monkeypatch.setattr("src.utils.adaptive.PARAMS_FILE", params_file)
    monkeypatch.setattr(WorkflowEnhancer, "MOMENTUM_FILE", tmp_path / "momentum.json")
    journal = TradingJournal(db_path=str(tmp_path / "wf.db"))
    return WorkflowEnhancer(journal=journal), params_file, journal


def test_heuristic_fallback_returns_3_suggestions(isolated):
    enhancer, _, _ = isolated
    assert len(enhancer._heuristic_suggestions(_POOR)) == 3


def test_auto_apply_saves_params(isolated):
    enhancer, params_file, journal = isolated
    _seed_real_trades(journal, 16)
    enhancer.run(_POOR)   # primes momentum (no apply on first same-dir suggestion)
    enhancer.run(_POOR)   # second consistent suggestion → applies
    saved = json.loads(open(params_file, encoding="utf-8").read())
    assert saved["scout_rsi_low"] == 55


def test_applied_changes_returned(isolated):
    enhancer, _, journal = isolated
    _seed_real_trades(journal, 16)
    enhancer.run(_POOR)
    result = enhancer.run(_POOR)
    assert len(result["auto_applied"]) >= 1


def test_params_within_bounds_always(isolated):
    enhancer, params_file, journal = isolated
    _seed_real_trades(journal, 16)
    enhancer.run(_POOR)
    enhancer.run(_POOR)
    saved = json.loads(open(params_file, encoding="utf-8").read())
    for name, value in saved.items():
        if name in ADJUSTABLE_PARAMS:
            assert ADJUSTABLE_PARAMS[name]["min"] <= value <= ADJUSTABLE_PARAMS[name]["max"]
