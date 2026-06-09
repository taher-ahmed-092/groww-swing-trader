"""Workflow enhancer behavior (heuristic path — no API key in test env)."""
from __future__ import annotations

import json

import pytest

from src.agents.meta.workflow_enhancer import WorkflowEnhancer
from src.memory.journal import TradingJournal
from src.utils.adaptive import ADJUSTABLE_PARAMS

_POOR = {
    "fundamental": {"score_correlation": 0.05, "trades_analyzed": 10},
    "technical": {"buy_signal_win_rate": 0.4},
    "judge": {"avg_calibration_error": 0.25},
}


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    params_file = str(tmp_path / "adaptive_params.json")
    monkeypatch.setattr("src.utils.adaptive.PARAMS_FILE", params_file)
    journal = TradingJournal(db_path=str(tmp_path / "wf.db"))
    return WorkflowEnhancer(journal=journal), params_file


def test_heuristic_fallback_returns_3_suggestions(isolated):
    enhancer, _ = isolated
    suggestions = enhancer._heuristic_suggestions(_POOR)
    assert len(suggestions) == 3


def test_auto_apply_saves_params(isolated):
    enhancer, params_file = isolated
    enhancer.run(_POOR)
    saved = json.loads(open(params_file, encoding="utf-8").read())
    assert saved["scout_rsi_low"] == 55


def test_applied_changes_returned(isolated):
    enhancer, _ = isolated
    result = enhancer.run(_POOR)
    assert len(result["auto_applied"]) >= 1


def test_params_within_bounds_always(isolated):
    enhancer, params_file = isolated
    enhancer.run(_POOR)
    saved = json.loads(open(params_file, encoding="utf-8").read())
    for name, value in saved.items():
        if name in ADJUSTABLE_PARAMS:
            assert ADJUSTABLE_PARAMS[name]["min"] <= value <= ADJUSTABLE_PARAMS[name]["max"]
