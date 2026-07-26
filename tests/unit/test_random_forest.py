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


def test_feature_extraction_top_level_fallback():
    """continuous-sim-shaped records (src/learning/continuous_simulator.py
    _simulate_window) store rsi/trend/adx as top-level keys with no
    indicators_snapshot dict — the extractor must fall back to them instead
    of silently defaulting to rsi=50/adx_sig=NEUTRAL/trend=SIDEWAYS."""
    model = RandomForestModel()
    record = {
        "symbol": "RELIANCE", "tier": "large", "entry": 1428.59,
        "rsi": 59.2091, "trend": "UPTREND", "adx": "TRENDING",
        "outcome": "WIN",
    }
    feat = model._extract_features(record)
    assert feat is not None
    rsi_i = FEATURES.index("rsi")
    trend_up_i = FEATURES.index("trend_up")
    adx_trending_i = FEATURES.index("adx_trending")
    assert feat[rsi_i] == 59.2091
    assert feat[trend_up_i] == 1.0
    assert feat[adx_trending_i] == 1.0


def test_feature_extraction_prefers_nested_indicators_snapshot():
    """Records that DO carry indicators_snapshot must keep using the nested
    values even if top-level rsi/trend/adx are also present (regression
    guard: nested data must win over the new top-level fallback)."""
    model = RandomForestModel()
    record = {
        "tier": "large",
        "rsi": 10, "trend": "DOWNTREND", "adx": "CHOPPY",
        "indicators_snapshot": {
            "rsi_14": 55, "adx_signal": "TRENDING", "trend": "UPTREND",
        },
    }
    feat = model._extract_features(record)
    assert feat is not None
    rsi_i = FEATURES.index("rsi")
    trend_up_i = FEATURES.index("trend_up")
    adx_trending_i = FEATURES.index("adx_trending")
    assert feat[rsi_i] == 55
    assert feat[trend_up_i] == 1.0
    assert feat[adx_trending_i] == 1.0


def test_predict_returns_none_no_model(tmp_path, monkeypatch):
    monkeypatch.setattr("src.ml.random_forest_model.MODEL_FILE", tmp_path / "missing.joblib")
    result = RandomForestModel().predict_win_probability({"rsi_14": 55})
    assert result is None
