"""
Random Forest signal classifier trained on historical replay/simulation data.
Activates when 50+ simulation data points exist (not 30 real trades) — the
real pipeline has 0 trades so far, but the forced-learning engine already has
hundreds. More interpretable than XGBoost: we can see which features matter.

Features: RSI, ADX value, CMF, volume ratio, ATR%, trend (encoded), OBV,
supertrend, tier (encoded), RSI momentum zone. Deliberately excludes the
rule-based signal score — see _extract_features' docstring for why.
Target: WIN (1) or LOSS (0).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

MODEL_FILE = Path("data/models/random_forest.json")
FEATURE_IMPORTANCE_FILE = Path("data/cache/rf_feature_importance.json")
MIN_SAMPLES = 50

FEATURES = [
    "rsi", "adx_value", "cmf",
    "vol_ratio", "atr_pct",          # independent of the rule-based score
    "trend_up", "trend_down",       # one-hot
    "adx_trending", "adx_choppy",   # one-hot
    "tier_large", "tier_mid",       # one-hot
    "obv_rising",                    # binary
    "supertrend_bull",               # binary
    "above_vwap",                    # binary
    "rsi_zone",                      # binary: 50<=rsi<=65 momentum zone
]


class RandomForestModel:
    def _extract_features(self, record: dict) -> list | None:
        """Extracts feature vector from a trade record or indicator dict.

        Deliberately excludes the rule-based `score`/`signal_score` field — an
        earlier version included it and it ended up with 84% of the trained
        model's feature importance (audit finding), meaning the "independent"
        ML opinion was mostly just re-reading a number the rule-based scorer
        already computed. Features here are raw indicators only.
        """
        try:
            ind = record.get("indicators_snapshot", record.get("indicators", {})) or {}
            # Continuous-sim/forced-trade records (src/learning/continuous_simulator.py
            # _simulate_window) store rsi/trend/adx as TOP-LEVEL keys with no nested
            # indicators_snapshot dict at all, so fall back to the record itself.
            rsi = ind.get("rsi_at_entry", ind.get("rsi_14", record.get("rsi", 50))) or 50
            adx_value = ind.get("adx_14", 0) or 0
            adx_sig = ind.get("adx_at_entry", ind.get("adx_signal", record.get("adx", "NEUTRAL"))) or "NEUTRAL"
            trend = ind.get("trend_at_entry", ind.get("trend", record.get("trend", "SIDEWAYS"))) or "SIDEWAYS"
            cmf = ind.get("cmf_20", 0) or 0
            tier = record.get("tier", "large") or "large"
            obv = ind.get("obv_trend", "NEUTRAL") == "RISING"
            st = ind.get("supertrend_direction", "NEUTRAL") == "BULLISH"
            vwap = ind.get("price_vs_vwap", "NEUTRAL") == "ABOVE"

            vol_ratio = ind.get("volume_ratio", 1.0) or 1.0
            atr = ind.get("atr_14", 0) or 0
            entry = record.get("entry", record.get("entry_price", 100)) or 100
            atr_pct = (atr / entry * 100) if entry else 0.0
            rsi_zone = 1.0 if 50 <= rsi <= 65 else 0.0

            return [
                float(rsi), float(adx_value), float(cmf),
                float(vol_ratio), float(atr_pct),
                1.0 if trend == "UPTREND" else 0.0,
                1.0 if trend == "DOWNTREND" else 0.0,
                1.0 if adx_sig == "TRENDING" else 0.0,
                1.0 if adx_sig == "CHOPPY" else 0.0,
                1.0 if tier == "large" else 0.0,
                1.0 if tier == "mid" else 0.0,
                1.0 if obv else 0.0,
                1.0 if st else 0.0,
                1.0 if vwap else 0.0,
                rsi_zone,
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
            # forced_trades_history.json records carry no rsi/adx/trend fields at
            # all (only a free-text "rationale" string) — continuous_sim_history.json
            # is the only source that stores those as usable top-level values, so
            # excluding it left rsi/adx with zero variance across the training set.
            "data/cache/continuous_sim_history.json",
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
            log.warning(
                "RF model accuracy insufficient without the score crutch "
                "(%.0f%% < 52%%) — disabling rather than deploying noise as signal.",
                val_acc * 100)
            # Actively disable — remove any previously-deployed model so
            # predict_win_probability() returns None instead of serving stale
            # predictions from before this feature change.
            for stale in (MODEL_FILE, FEATURE_IMPORTANCE_FILE):
                try:
                    stale.unlink(missing_ok=True)
                except OSError:
                    pass
            return {"trained": False, "n_samples": len(X), "val_accuracy": round(val_acc, 3),
                    "reason": "insufficient_signal_without_crutch",
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
