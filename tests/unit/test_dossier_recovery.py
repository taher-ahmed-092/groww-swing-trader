"""Regime-aware dossier warm-up: a stock whose last trade was a LOSS and
whose technicals have since turned favorable (downtrend->uptrend, or RSI
recovered from oversold) gets flagged as a recovery candidate."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pandas as pd

from src.memory.company_dossier import find_recovery_candidates


def _fake_fetcher(indicators: dict) -> MagicMock:
    fetcher = MagicMock()
    fetcher.get_price_history.return_value = pd.DataFrame({"Close": range(40)})
    return fetcher, indicators


def test_downtrend_to_uptrend_flagged_as_recovery(tmp_path, monkeypatch):
    dossier_dir = tmp_path / "dossiers"
    dossier_dir.mkdir()
    (dossier_dir / "SUNPHARMA.json").write_text(json.dumps({
        "symbol": "SUNPHARMA",
        "trade_history": [{"outcome": "LOSS"}],
        "technical_snapshots": [{"trend": "DOWNTREND", "rsi_14": 50}],
    }))
    monkeypatch.setattr("src.memory.company_dossier.dossier_store.base_dir", dossier_dir)

    fetcher, _ = _fake_fetcher({})
    monkeypatch.setattr(
        "src.agents.technical.indicators.compute_indicators",
        lambda df: {"trend": "UPTREND", "rsi_14": 50})

    candidates = find_recovery_candidates(fetcher=fetcher)
    assert "SUNPHARMA" in candidates


def test_oversold_recovery_flagged(tmp_path, monkeypatch):
    dossier_dir = tmp_path / "dossiers"
    dossier_dir.mkdir()
    (dossier_dir / "WIPRO.json").write_text(json.dumps({
        "symbol": "WIPRO",
        "trade_history": [{"outcome": "LOSS"}],
        "technical_snapshots": [{"trend": "SIDEWAYS", "rsi_14": 28}],
    }))
    monkeypatch.setattr("src.memory.company_dossier.dossier_store.base_dir", dossier_dir)

    fetcher, _ = _fake_fetcher({})
    monkeypatch.setattr(
        "src.agents.technical.indicators.compute_indicators",
        lambda df: {"trend": "SIDEWAYS", "rsi_14": 45})

    candidates = find_recovery_candidates(fetcher=fetcher)
    assert "WIPRO" in candidates


def test_last_trade_win_not_considered(tmp_path, monkeypatch):
    dossier_dir = tmp_path / "dossiers"
    dossier_dir.mkdir()
    (dossier_dir / "TCS.json").write_text(json.dumps({
        "symbol": "TCS",
        "trade_history": [{"outcome": "WIN"}],
        "technical_snapshots": [{"trend": "DOWNTREND", "rsi_14": 50}],
    }))
    monkeypatch.setattr("src.memory.company_dossier.dossier_store.base_dir", dossier_dir)

    fetcher, _ = _fake_fetcher({})
    monkeypatch.setattr(
        "src.agents.technical.indicators.compute_indicators",
        lambda df: {"trend": "UPTREND", "rsi_14": 50})

    candidates = find_recovery_candidates(fetcher=fetcher)
    assert "TCS" not in candidates


def test_no_improvement_not_flagged(tmp_path, monkeypatch):
    dossier_dir = tmp_path / "dossiers"
    dossier_dir.mkdir()
    (dossier_dir / "GLAND.json").write_text(json.dumps({
        "symbol": "GLAND",
        "trade_history": [{"outcome": "LOSS"}],
        "technical_snapshots": [{"trend": "DOWNTREND", "rsi_14": 50}],
    }))
    monkeypatch.setattr("src.memory.company_dossier.dossier_store.base_dir", dossier_dir)

    fetcher, _ = _fake_fetcher({})
    monkeypatch.setattr(
        "src.agents.technical.indicators.compute_indicators",
        lambda df: {"trend": "DOWNTREND", "rsi_14": 48})

    candidates = find_recovery_candidates(fetcher=fetcher)
    assert "GLAND" not in candidates
