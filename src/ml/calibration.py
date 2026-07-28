"""Calibrated ML ensemble (Fix 5).

A raw tree-ensemble `predict_proba` is notoriously overconfident near 0/1 —
"trustworthy probabilities" means running the classifier through
sklearn.calibration.CalibratedClassifierCV (isotonic, cv=3) before it's ever
shown as a number. This wraps a fresh RandomForestClassifier + a
LogisticRegression baseline, both trained on the same simulation-derived
feature space as src.ml.random_forest_model.RandomForestModel (reuses its
`_extract_features` and training-data loader so the two models see identical
inputs — a prerequisite for a meaningful ensemble mean).

XGBoost (src.ml.signal_combiner.SignalCombiner) trains on a DIFFERENT gate
(30+ REAL pipeline trades, vs this module's 50+ simulation trades) via its
own custom save/load pipeline; it is NOT rewrapped in CalibratedClassifierCV
here (that would mean rewriting SignalCombiner's training loop, a separate,
larger change). Its raw probability is still folded into the ensemble mean
when available — get_status() flags it explicitly as "not yet calibrated" so
this limitation is visible, not silently glossed over.

Ensemble = mean of every AVAILABLE probability. If available models disagree
by more than MODEL_DISAGREEMENT_THRESHOLD, ensemble_probability is withheld
entirely and disagreement=True / flag="MODEL_DISAGREEMENT" — callers must not
use the ML probability in scoring for that candidate. ML stays advisory-only
until 30 real pipeline trades regardless (CLAUDE.md rule 4 — LLMs/ML never
compute the numbers that gate a trade; unchanged by this module).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

MODEL_DIR = Path("data/models")
CALIBRATED_RF_FILE = MODEL_DIR / "calibrated_rf.joblib"
CALIBRATED_LOGREG_FILE = MODEL_DIR / "calibrated_logreg.joblib"
BRIER_SCORES_FILE = Path("data/cache/ml_brier_scores.json")

MODEL_DISAGREEMENT_THRESHOLD = 0.25
MIN_TRADES_TO_TRAIN_REAL = 30  # unchanged — ML stays advisory-only until this many real trades

_SIM_HISTORY_FILES = (
    "data/cache/forced_trades_history.json",
    "data/cache/intraday_sim_history.json",
    "data/cache/short_trades_history.json",
    "data/cache/continuous_sim_history.json",
)


class CalibratedEnsemble:
    def __init__(self) -> None:
        from src.ml.random_forest_model import MIN_SAMPLES, RandomForestModel

        self._rf_helper = RandomForestModel()  # reuse _extract_features only
        self.min_samples = MIN_SAMPLES

    def _load_training_data(self) -> list[dict]:
        all_trades: list[dict] = []
        for fname in _SIM_HISTORY_FILES:
            p = Path(fname)
            if not p.exists():
                continue
            try:
                for t in json.loads(p.read_text()):
                    if t.get("outcome") in ("WIN", "LOSS"):
                        all_trades.append(t)
            except Exception:
                pass
        return all_trades

    def train(self) -> dict:
        all_trades = self._load_training_data()
        if len(all_trades) < self.min_samples:
            return {"trained": False, "n_samples": len(all_trades),
                    "message": f"Need {self.min_samples}, have {len(all_trades)}"}

        X, y = [], []
        for t in all_trades:
            feat = self._rf_helper._extract_features(t)
            if feat:
                X.append(feat)
                y.append(1 if t["outcome"] == "WIN" else 0)
        if len(X) < self.min_samples:
            return {"trained": False, "n_samples": len(X)}

        try:
            import numpy as np
            from sklearn.calibration import CalibratedClassifierCV
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.linear_model import LogisticRegression
            from sklearn.metrics import brier_score_loss
        except ImportError:
            return {"trained": False, "message": "sklearn not installed — run: uv add scikit-learn"}

        X_arr, y_arr = np.array(X), np.array(y)
        split = int(len(X_arr) * 0.7)
        X_train, X_val = X_arr[:split], X_arr[split:]
        y_train, y_val = y_arr[:split], y_arr[split:]
        if len(set(y_train.tolist())) < 2 or len(y_val) == 0:
            return {"trained": False, "message": "insufficient class diversity to calibrate"}

        cal_rf = CalibratedClassifierCV(
            RandomForestClassifier(n_estimators=100, max_depth=4, min_samples_leaf=8,
                                   random_state=42, class_weight="balanced"),
            method="isotonic", cv=3)
        cal_logreg = CalibratedClassifierCV(
            LogisticRegression(max_iter=1000, class_weight="balanced"),
            method="isotonic", cv=3)

        models = {"random_forest": cal_rf, "logistic_regression": cal_logreg}
        brier_scores = {}
        for name, model in models.items():
            model.fit(X_train, y_train)
            probs = model.predict_proba(X_val)[:, 1]
            brier_scores[name] = round(float(brier_score_loss(y_val, probs)), 4)

        try:
            import joblib

            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            joblib.dump(models["random_forest"], str(CALIBRATED_RF_FILE))
            joblib.dump(models["logistic_regression"], str(CALIBRATED_LOGREG_FILE))
            BRIER_SCORES_FILE.parent.mkdir(parents=True, exist_ok=True)
            BRIER_SCORES_FILE.write_text(json.dumps(brier_scores))
        except ImportError:
            return {"trained": False, "message": "joblib not installed — run: uv add joblib"}

        return {"trained": True, "n_samples": len(X), "brier_scores": brier_scores}

    def predict(self, indicators: dict, trade_meta: dict | None = None) -> dict:
        """Never raises. Returns:
          ensemble_probability: float|None (None if withheld on disagreement
            or no model available)
          model_probabilities: {model_name: probability}
          disagreement: bool
          flag: "MODEL_DISAGREEMENT"|None
        """
        probs: dict[str, float] = {}
        record = {**(trade_meta or {}), "indicators_snapshot": indicators}
        feat = self._rf_helper._extract_features(record)

        if feat is not None:
            try:
                import joblib
                import numpy as np

                if CALIBRATED_RF_FILE.exists():
                    model = joblib.load(str(CALIBRATED_RF_FILE))
                    probs["random_forest"] = float(model.predict_proba(np.array([feat]))[0][1])
                if CALIBRATED_LOGREG_FILE.exists():
                    model = joblib.load(str(CALIBRATED_LOGREG_FILE))
                    probs["logistic_regression"] = float(model.predict_proba(np.array([feat]))[0][1])
            except Exception:
                pass

        try:
            from src.ml.signal_combiner import SignalCombiner

            xgb_prob = SignalCombiner().predict_win_probability(trade_meta or {})
            if xgb_prob is not None:
                probs["xgboost"] = float(xgb_prob)
        except Exception:
            pass

        if not probs:
            return {"ensemble_probability": None, "model_probabilities": {},
                    "disagreement": False, "flag": None}

        values = list(probs.values())
        disagreement = (max(values) - min(values)) > MODEL_DISAGREEMENT_THRESHOLD if len(values) > 1 else False

        return {
            "ensemble_probability": None if disagreement else round(sum(values) / len(values), 4),
            "model_probabilities": {k: round(v, 4) for k, v in probs.items()},
            "disagreement": disagreement,
            "flag": "MODEL_DISAGREEMENT" if disagreement else None,
        }

    @staticmethod
    def get_brier_scores() -> dict:
        if BRIER_SCORES_FILE.exists():
            try:
                return json.loads(BRIER_SCORES_FILE.read_text())
            except Exception:
                pass
        return {}

    @staticmethod
    def get_status() -> dict:
        """Reported by /learn — per-model Brier score plus the explicit
        calibration caveat for XGBoost."""
        return {
            "calibrated_models": ["random_forest", "logistic_regression"],
            "uncalibrated_models": ["xgboost (own training gate/pipeline — see module docstring)"],
            "brier_scores": CalibratedEnsemble.get_brier_scores(),
            "disagreement_threshold": MODEL_DISAGREEMENT_THRESHOLD,
        }
