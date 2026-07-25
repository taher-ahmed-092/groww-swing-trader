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
STATUS_FILE = Path("data/models/signal_combiner_status.json")
MIN_TRADES_TO_TRAIN = 30


def get_status(journal=None) -> dict:
    """Single source of truth for XGBoost status — the dashboard progress bar,
    the "AI Models Active" panel, and /report and /learn all call this instead
    of each computing their own (previously divergent) formula. Progress is
    measured in REAL pipeline trades only, matching what actually gates
    train() — forced/sim trade counts were being blended into some of the old
    formulas even though the model never trains on them."""
    from src.memory.journal import TradingJournal

    journal = journal or TradingJournal()
    closed = len([t for t in journal.get_recent(n=200) if t.outcome in ("WIN", "LOSS")])
    progress_pct = min(100, round(closed / MIN_TRADES_TO_TRAIN * 100))

    if MODEL_FILE.exists():
        val_accuracy = None
        if STATUS_FILE.exists():
            try:
                val_accuracy = json.loads(STATUS_FILE.read_text()).get("val_accuracy")
            except Exception:
                pass
        label = (f"Active (val acc {val_accuracy:.0f}%)" if val_accuracy is not None
                 else "Active")
        return {"active": True, "label": label, "val_accuracy": val_accuracy,
                "progress_pct": 100, "n_real_trades": closed, "advisory_only": True}

    return {"active": False,
            "label": f"Waiting for real trades ({closed}/{MIN_TRADES_TO_TRAIN})",
            "val_accuracy": None, "progress_pct": progress_pct,
            "n_real_trades": closed, "advisory_only": True}


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

        # Walk-forward validation: train on the first 70% chronologically, validate
        # on the last 30%. A model that only looks good on data it trained on is
        # the overfitting/decay trap — it fits historical noise and collapses live.
        split = int(len(X) * 0.7)
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]

        val_model = xgb.XGBClassifier(n_estimators=50, max_depth=3,
                                      learning_rate=0.1, eval_metric="logloss")
        val_model.fit(X_train, y_train)
        val_preds = val_model.predict(X_val)
        val_accuracy = float((val_preds == y_val).mean()) if len(y_val) else 0.0

        if val_accuracy * 100 <= 55:
            return {"trained": False, "n_samples": len(X),
                    "reason": f"validation accuracy {val_accuracy * 100:.0f}% below 55% — "
                              "model would overfit; accumulating more data"}

        # Validation passed — retrain on the full dataset for the deployed model.
        model = xgb.XGBClassifier(n_estimators=50, max_depth=3,
                                  learning_rate=0.1, eval_metric="logloss")
        model.fit(X, y)
        self.MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        model.save_model(str(self.MODEL_FILE))
        try:
            STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATUS_FILE.write_text(json.dumps({"val_accuracy": round(val_accuracy * 100, 1)}))
        except OSError:
            pass

        names = list(self.extract_features({}).keys())
        top = sorted(zip(names, model.feature_importances_), key=lambda x: -x[1])[:5]
        return {"trained": True, "n_samples": len(X),
                "validation_accuracy": round(val_accuracy * 100, 1),
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
