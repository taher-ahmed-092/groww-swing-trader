"""RF-confirmed RSI insight converts into an immediate judge score penalty:
once RF's own feature importance says RSI matters (>0.3), an entry landing
in the known-loss zone (RSI>65 or <35) inside CHOPPY ADX gets -0.5."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.judge.evaluator import LLMJudge


def _technical(rsi, adx_signal):
    return {"indicators": {"rsi_14": rsi, "adx_signal": adx_signal}}


@patch("src.ml.random_forest_model.RandomForestModel")
def test_penalty_applies_when_rsi_confirmed_and_in_loss_zone(mock_rf_cls):
    mock_rf_cls.return_value.get_feature_importance.return_value = {"rsi": 0.4}
    penalty = LLMJudge._rf_confirmed_rsi_penalty(_technical(rsi=70, adx_signal="CHOPPY"))
    assert penalty == -0.5


@patch("src.ml.random_forest_model.RandomForestModel")
def test_no_penalty_when_rsi_importance_below_threshold(mock_rf_cls):
    mock_rf_cls.return_value.get_feature_importance.return_value = {"rsi": 0.1}
    penalty = LLMJudge._rf_confirmed_rsi_penalty(_technical(rsi=70, adx_signal="CHOPPY"))
    assert penalty == 0.0


@patch("src.ml.random_forest_model.RandomForestModel")
def test_no_penalty_when_adx_not_choppy(mock_rf_cls):
    mock_rf_cls.return_value.get_feature_importance.return_value = {"rsi": 0.4}
    penalty = LLMJudge._rf_confirmed_rsi_penalty(_technical(rsi=70, adx_signal="TRENDING"))
    assert penalty == 0.0


@patch("src.ml.random_forest_model.RandomForestModel")
def test_no_penalty_when_rsi_in_normal_range(mock_rf_cls):
    mock_rf_cls.return_value.get_feature_importance.return_value = {"rsi": 0.4}
    penalty = LLMJudge._rf_confirmed_rsi_penalty(_technical(rsi=50, adx_signal="CHOPPY"))
    assert penalty == 0.0
