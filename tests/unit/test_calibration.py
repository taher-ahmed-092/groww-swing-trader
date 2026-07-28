"""Calibrated ML ensemble (Fix 5) — disagreement gating and Brier-score
persistence. Model training itself (CalibratedClassifierCV) is exercised
indirectly via test_random_forest.py's data-gating conventions; here we test
the ensemble-specific logic: insufficient-data gating, disagreement
withholding, and Brier score round-tripping."""
from __future__ import annotations

import json

import src.ml.calibration as cal_mod
from src.ml.calibration import CalibratedEnsemble, MODEL_DISAGREEMENT_THRESHOLD


def test_train_reports_insufficient_samples(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CalibratedEnsemble().train()
    assert result["trained"] is False
    assert result["n_samples"] == 0


def test_predict_returns_none_when_no_models_available(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ensemble = CalibratedEnsemble()
    monkeypatch.setattr(ensemble._rf_helper, "_extract_features", lambda record: None)
    result = ensemble.predict({}, {})
    assert result["ensemble_probability"] is None
    assert result["model_probabilities"] == {}
    assert result["disagreement"] is False


def test_predict_withholds_probability_on_disagreement(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ensemble = CalibratedEnsemble()
    monkeypatch.setattr(ensemble._rf_helper, "_extract_features", lambda record: [1.0] * 15)

    class _FakeModel:
        def __init__(self, prob):
            self._prob = prob

        def predict_proba(self, X):
            return [[1 - self._prob, self._prob]]

    monkeypatch.setattr(cal_mod, "CALIBRATED_RF_FILE", type("P", (), {"exists": lambda self=None: True})())
    monkeypatch.setattr(cal_mod, "CALIBRATED_LOGREG_FILE", type("P", (), {"exists": lambda self=None: True})())

    probs_iter = iter([_FakeModel(0.9), _FakeModel(0.2)])  # disagree by 0.7 > threshold
    monkeypatch.setattr("joblib.load", lambda path: next(probs_iter))

    result = ensemble.predict({"rsi_14": 55}, {})
    assert result["disagreement"] is True
    assert result["ensemble_probability"] is None
    assert result["flag"] == "MODEL_DISAGREEMENT"
    assert len(result["model_probabilities"]) == 2


def test_predict_averages_when_models_agree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ensemble = CalibratedEnsemble()
    monkeypatch.setattr(ensemble._rf_helper, "_extract_features", lambda record: [1.0] * 15)

    class _FakeModel:
        def __init__(self, prob):
            self._prob = prob

        def predict_proba(self, X):
            return [[1 - self._prob, self._prob]]

    monkeypatch.setattr(cal_mod, "CALIBRATED_RF_FILE", type("P", (), {"exists": lambda self=None: True})())
    monkeypatch.setattr(cal_mod, "CALIBRATED_LOGREG_FILE", type("P", (), {"exists": lambda self=None: True})())

    probs_iter = iter([_FakeModel(0.6), _FakeModel(0.65)])  # agree within threshold
    monkeypatch.setattr("joblib.load", lambda path: next(probs_iter))

    result = ensemble.predict({"rsi_14": 55}, {})
    assert result["disagreement"] is False
    assert result["ensemble_probability"] == round((0.6 + 0.65) / 2, 4)


def test_brier_scores_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(cal_mod, "BRIER_SCORES_FILE", tmp_path / "brier.json")
    cal_mod.BRIER_SCORES_FILE.write_text(json.dumps({"random_forest": 0.18}))
    assert CalibratedEnsemble.get_brier_scores() == {"random_forest": 0.18}


def test_get_status_flags_xgboost_as_uncalibrated():
    status = CalibratedEnsemble.get_status()
    assert "xgboost (own training gate/pipeline — see module docstring)" in status["uncalibrated_models"]
    assert status["disagreement_threshold"] == MODEL_DISAGREEMENT_THRESHOLD
