"""Random Forest signal classifier — trained on simulation data."""
from __future__ import annotations

from src.ml.random_forest_model import FEATURES, RandomForestModel


def test_insufficient_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = RandomForestModel().train()
    assert result["trained"] is False


def test_feature_extraction_complete():
    model = RandomForestModel()
    record = {
        "signal_score": 0.7, "tier": "large",
        "indicators": {
            "rsi_14": 55, "adx_signal": "TRENDING", "trend": "UPTREND",
            "cmf_20": 0.1, "obv_trend": "RISING",
            "supertrend_direction": "BULLISH", "price_vs_vwap": "ABOVE",
        },
    }
    feat = model._extract_features(record)
    assert feat is not None
    assert len(feat) == len(FEATURES)


def test_predict_returns_none_no_model(tmp_path, monkeypatch):
    monkeypatch.setattr("src.ml.random_forest_model.MODEL_FILE", tmp_path / "missing.joblib")
    result = RandomForestModel().predict_win_probability({"rsi_14": 55})
    assert result is None
