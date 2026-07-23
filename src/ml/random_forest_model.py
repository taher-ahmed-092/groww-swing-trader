"""
Random Forest signal classifier trained on historical replay/simulation data.
Activates when 50+ simulation data points exist (not 30 real trades) — the
real pipeline has 0 trades so far, but the forced-learning engine already has
hundreds. More interpretable than XGBoost: we can see which features matter.

Features: RSI, ADX value, trend (encoded), OBV, supertrend, CMF, 52W position,
score, tier (encoded). Target: WIN (1) or LOSS (0).
"""
from __future__ import annotations

import json
from pathlib import Path

MODEL_FILE = Path("data/models/random_forest.json")
FEATURE_IMPORTANCE_FILE = Path("data/cache/rf_feature_importance.json")
MIN_SAMPLES = 50

FEATURES = [
    "rsi", "adx_value", "cmf", "score",
    "trend_up", "trend_down",       # one-hot
    "adx_trending", "adx_choppy",   # one-hot
    "tier_large", "tier_mid",       # one-hot
    "obv_rising",                    # binary
    "supertrend_bull",               # binary
    "above_vwap",                    # binary
]


class RandomForestModel:
    def _extract_features(self, record: dict) -> list | None:
        """Extracts feature vector from a trade record or indicator dict."""
        try:
            ind = record.get("indicators_snapshot", record.get("indicators", {})) or {}
            rsi = ind.get("rsi_at_entry", ind.get("rsi_14", 50)) or 50
            adx_sig = ind.get("adx_at_entry", ind.get("adx_signal", "NEUTRAL")) or "NEUTRAL"
            trend = ind.get("trend_at_entry", ind.get("trend", "SIDEWAYS")) or "SIDEWAYS"
            score = record.get("signal_score", record.get("score", 0.5)) or 0.5
            cmf = ind.get("cmf_20", 0) or 0
            tier = record.get("tier", "large") or "large"
            obv = ind.get("obv_trend", "NEUTRAL") == "RISING"
            st = ind.get("supertrend_direction", "NEUTRAL") == "BULLISH"
            vwap = ind.get("price_vs_vwap", "NEUTRAL") == "ABOVE"

            return [
                float(rsi), 0.0, float(cmf), float(score),
                1.0 if trend == "UPTREND" else 0.0,
                1.0 if trend == "DOWNTREND" else 0.0,
                1.0 if adx_sig == "TRENDING" else 0.0,
                1.0 if adx_sig == "CHOPPY" else 0.0,
                1.0 if tier == "large" else 0.0,
                1.0 if tier == "mid" else 0.0,
                1.0 if obv else 0.0,
                1.0 if st else 0.0,
                1.0 if vwap else 0.0,
            ]
        except Exception:
            return None

    def train(self) -> dict:
        """Trains on ALL available simulation data. Walk-forward validated;
        only deployed if validation accuracy clears 52% (avoids overfitting
        on a shallow, interpretable model)."""
        all_trades: list[dict] = []
        for fname in (
            "data/cache/forced_trades_history.json",
            "data/cache/intraday_sim_history.json",
            "data/cache/short_trades_history.json",
        ):
            p = Path(fname)
            if p.exists():
                try:
                    for t in json.loads(p.read_text()):
                        if t.get("outcome") in ("WIN", "LOSS"):
                            all_trades.append(t)
                except Exception:
                    pass

        if len(all_trades) < MIN_SAMPLES:
            return {"trained": False, "n_samples": len(all_trades),
                    "message": f"Need {MIN_SAMPLES}, have {len(all_trades)}"}

        X, y = [], []
        for t in all_trades:
            feat = self._extract_features(t)
            if feat:
                X.append(feat)
                y.append(1 if t["outcome"] == "WIN" else 0)

        if len(X) < MIN_SAMPLES:
            return {"trained": False, "n_samples": len(X)}

        try:
            import numpy as np
            from sklearn.ensemble import RandomForestClassifier
        except ImportError:
            return {"trained": False,
                    "message": "sklearn not installed — run: uv add scikit-learn"}

        X_arr, y_arr = np.array(X), np.array(y)

        # Walk-forward split: train first 70%, validate last 30%.
        split = int(len(X_arr) * 0.7)
        X_train, X_val = X_arr[:split], X_arr[split:]
        y_train, y_val = y_arr[:split], y_arr[split:]

        model = RandomForestClassifier(
            n_estimators=100, max_depth=4, min_samples_leaf=8,
            random_state=42, class_weight="balanced")
        model.fit(X_train, y_train)
        val_acc = model.score(X_val, y_val) if len(y_val) else 0.0

        if val_acc < 0.52:
            return {"trained": False, "n_samples": len(X), "val_accuracy": round(val_acc, 3),
                    "message": f"Val accuracy {val_acc:.0%} < 52% — avoiding overfit"}

        importances = dict(zip(FEATURES, model.feature_importances_.tolist()))
        top = sorted(importances.items(), key=lambda x: -x[1])[:5]

        try:
            import joblib

            MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(model, str(MODEL_FILE))
            FEATURE_IMPORTANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
            FEATURE_IMPORTANCE_FILE.write_text(json.dumps(importances))
        except ImportError:
            return {"trained": False, "message": "joblib not installed — run: uv add joblib"}

        return {"trained": True, "n_samples": len(X), "val_accuracy": round(val_acc, 3),
                "top_features": [(f, round(v, 3)) for f, v in top]}

    def predict_win_probability(self, indicators: dict, trade_meta: dict | None = None) -> float | None:
        """Returns win probability 0-1 or None if model not available."""
        if not MODEL_FILE.exists():
            return None
        try:
            import joblib
            import numpy as np

            model = joblib.load(str(MODEL_FILE))
            record = {**(trade_meta or {}), "indicators_snapshot": indicators}
            feat = self._extract_features(record)
            if not feat:
                return None
            prob = model.predict_proba(np.array([feat]))[0][1]
            return round(float(prob), 3)
        except Exception:
            return None

    def get_feature_importance(self) -> dict:
        if FEATURE_IMPORTANCE_FILE.exists():
            try:
                return json.loads(FEATURE_IMPORTANCE_FILE.read_text())
            except Exception:
                pass
        return {}
