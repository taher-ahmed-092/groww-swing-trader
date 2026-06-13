"""XGBoost signal combiner — untrained behavior + feature extraction."""
from __future__ import annotations

from src.memory.journal import TradingJournal
from src.ml.signal_combiner import SignalCombiner


def test_predict_returns_none_untrained(tmp_path, monkeypatch):
    monkeypatch.setattr(SignalCombiner, "MODEL_FILE", tmp_path / "model.json")
    sc = SignalCombiner(journal=TradingJournal(db_path=str(tmp_path / "sc.db")))
    assert sc.predict_win_probability({}) is None


def test_extract_features_complete():
    state = {
        "fundamental_verdict": {"score": 0.8},
        "technical_verdict": {"score": 0.7, "indicators": {
            "rsi_14": 55, "adx_14": 28, "macd": 1.2, "volume_ratio": 1.5,
            "cmf_20": 0.1, "trend": "UPTREND", "adx_signal": "TRENDING",
            "obv_trend": "RISING", "supertrend_direction": "BULLISH",
            "price_vs_vwap": "ABOVE"}},
        "sentiment": {"sentiment_score": 0.3},
    }
    feats = SignalCombiner().extract_features(state)
    for key in ("fund_score", "tech_score", "rsi", "adx", "macd", "volume_ratio",
                "cmf", "sentiment", "trend_uptrend", "adx_trending", "obv_rising",
                "supertrend_bull", "above_vwap"):
        assert key in feats
    assert feats["trend_uptrend"] == 1 and feats["above_vwap"] == 1


def test_train_insufficient_data(tmp_path):
    sc = SignalCombiner(journal=TradingJournal(db_path=str(tmp_path / "sc.db")))
    result = sc.train()
    assert result["trained"] is False
