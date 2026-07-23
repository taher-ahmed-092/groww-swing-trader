"""Bayesian per-stock win priors (Beta distribution)."""
from __future__ import annotations

import json

from src.ml.stock_priors import StockPriors


def test_neutral_prior_new_stock(tmp_path, monkeypatch):
    monkeypatch.setattr("src.ml.stock_priors.PRIORS_FILE", tmp_path / "missing.json")
    assert StockPriors().get_win_prior("UNKNOWN_STOCK") == 0.5


def test_win_increases_prior(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.ml.stock_priors.PRIORS_FILE", tmp_path / "priors.json")
    (tmp_path / "data" / "cache").mkdir(parents=True)
    trades = [{"symbol": "WINNER", "outcome": "WIN"} for _ in range(5)]
    (tmp_path / "data" / "cache" / "forced_trades_history.json").write_text(json.dumps(trades))
    StockPriors().compute_and_save()
    assert StockPriors().get_win_prior("WINNER") > 0.5


def test_loss_decreases_prior(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.ml.stock_priors.PRIORS_FILE", tmp_path / "priors.json")
    (tmp_path / "data" / "cache").mkdir(parents=True)
    trades = [{"symbol": "LOSER", "outcome": "LOSS"} for _ in range(5)]
    (tmp_path / "data" / "cache" / "forced_trades_history.json").write_text(json.dumps(trades))
    StockPriors().compute_and_save()
    assert StockPriors().get_win_prior("LOSER") < 0.5


def test_conservative_with_few_samples(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.ml.stock_priors.PRIORS_FILE", tmp_path / "priors.json")
    (tmp_path / "data" / "cache").mkdir(parents=True)
    trades = [{"symbol": "SMALL_SAMPLE", "outcome": "WIN"}]
    (tmp_path / "data" / "cache" / "forced_trades_history.json").write_text(json.dumps(trades))
    StockPriors().compute_and_save()
    prior = StockPriors().get_win_prior("SMALL_SAMPLE")
    assert 0.5 <= prior <= 0.65
