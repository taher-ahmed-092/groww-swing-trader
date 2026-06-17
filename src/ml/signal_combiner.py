"""
XGBoost signal combiner — a lightweight, laptop-friendly signal AGGREGATOR.

Combines all signals (fundamental/technical scores, indicators, sentiment, regime
flags) into one calibrated win probability, learning from YOUR closed trades. Not a
black-box price predictor — it learns which signal COMBINATIONS predict wins. Trains
only after 30+ closed trades; before that, returns None and the judge score stands.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from src.memory.journal import TradingJournal

log = logging.getLogger(__name__)

# xgboost is an OPTIONAL extra (`uv sync --extra ml`). It has no Android/Termux
# wheel and compiles slowly, so the default install omits it. The combiner only
# activates after 30+ closed trades anyway; without it, predict returns None and
# the judge score stands — the system works fine.
try:
    import xgboost as xgb

    XGBOOST_AVAILABLE = True
except ImportError:
    xgb = None
    XGBOOST_AVAILABLE = False

MODEL_FILE = Path("data/models/signal_combiner.json")
MIN_TRADES_TO_TRAIN = 30


class SignalCombiner:
    MODEL_FILE = MODEL_FILE
    MIN_TRADES_TO_TRAIN = MIN_TRADES_TO_TRAIN

    def __init__(self, journal: TradingJournal | None = None) -> None:
        self.journal = journal or TradingJournal()

    def extract_features(self, state: dict) -> dict:
        tech = state.get("technical_verdict", {}) or {}
        ind = tech.get("indicators", {}) or {}
        fund = state.get("fundamental_verdict", {}) or {}
        return {
            "fund_score": fund.get("score", 0.5) or 0.5,
            "tech_score": tech.get("score", 0.5) or 0.5,
            "rsi": ind.get("rsi_14", 50) or 50,
            "adx": ind.get("adx_14", 20) or 20,
            "macd": ind.get("macd", 0) or 0,
            "volume_ratio": ind.get("volume_ratio", 1) or 1,
            "cmf": ind.get("cmf_20", 0) or 0,
            "sentiment": (state.get("sentiment", {}) or {}).get("sentiment_score", 0) or 0,
            "trend_uptrend": 1 if ind.get("trend") == "UPTREND" else 0,
            "adx_trending": 1 if ind.get("adx_signal") == "TRENDING" else 0,
            "obv_rising": 1 if ind.get("obv_trend") == "RISING" else 0,
            "supertrend_bull": 1 if ind.get("supertrend_direction") == "BULLISH" else 0,
            "above_vwap": 1 if ind.get("price_vs_vwap") == "ABOVE" else 0,
        }

    def train(self) -> dict:
        if not XGBOOST_AVAILABLE:
            return {"trained": False, "n_samples": 0,
                    "message": "XGBoost not installed. Install with: uv sync --extra ml"}
        closed = [t for t in self.journal.get_recent(n=200) if t.outcome in ("WIN", "LOSS")]
        if len(closed) < self.MIN_TRADES_TO_TRAIN:
            return {"trained": False, "n_samples": len(closed),
                    "message": f"Need {self.MIN_TRADES_TO_TRAIN}, have {len(closed)}"}

        import numpy as np

        X, y = [], []
        for t in closed:
            try:
                snap = json.loads(t.state_snapshot or "{}")
                X.append(list(self.extract_features(snap).values()))
                y.append(1 if t.outcome == "WIN" else 0)
            except (json.JSONDecodeError, TypeError):
                continue
        if len(X) < self.MIN_TRADES_TO_TRAIN:
            return {"trained": False, "n_samples": len(X)}

        X, y = np.array(X), np.array(y)
        model = xgb.XGBClassifier(n_estimators=50, max_depth=3,
                                  learning_rate=0.1, eval_metric="logloss")
        model.fit(X, y)
        self.MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        model.save_model(str(self.MODEL_FILE))

        names = list(self.extract_features({}).keys())
        top = sorted(zip(names, model.feature_importances_), key=lambda x: -x[1])[:5]
        return {"trained": True, "n_samples": len(X),
                "top_features": [(f, round(float(i), 3)) for f, i in top]}

    def predict_win_probability(self, state: dict) -> float | None:
        if not XGBOOST_AVAILABLE or not self.MODEL_FILE.exists():
            return None
        try:
            import numpy as np

            model = xgb.XGBClassifier()
            model.load_model(str(self.MODEL_FILE))
            features = list(self.extract_features(state).values())
            prob = model.predict_proba(np.array([features]))[0][1]
            return round(float(prob), 3)
        except Exception as exc:
            log.debug("SignalCombiner prediction failed: %s", exc)
            return None
