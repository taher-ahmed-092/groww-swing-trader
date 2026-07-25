"""XGBoost single status source — dashboard progress bar, AI Models panel,
and /report must all read the same function instead of three divergent
formulas (audit finding: one blended forced-trade volume in, another didn't)."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.ml.signal_combiner import MODEL_FILE, MIN_TRADES_TO_TRAIN, STATUS_FILE, get_status


def _fake_journal(n_closed: int):
    j = MagicMock()
    j.get_recent.return_value = [
        MagicMock(outcome="WIN" if i % 2 == 0 else "LOSS") for i in range(n_closed)
    ]
    return j


def test_waiting_for_real_trades_when_model_absent(tmp_path, monkeypatch):
    monkeypatch.setattr("src.ml.signal_combiner.MODEL_FILE", tmp_path / "missing.json")
    status = get_status(_fake_journal(5))
    assert status["active"] is False
    assert "Waiting for real trades" in status["label"]
    assert "5/" in status["label"]


def test_progress_pct_scales_with_real_trades_only(tmp_path, monkeypatch):
    monkeypatch.setattr("src.ml.signal_combiner.MODEL_FILE", tmp_path / "missing.json")
    status = get_status(_fake_journal(15))
    assert status["progress_pct"] == round(15 / MIN_TRADES_TO_TRAIN * 100)


def test_active_when_model_file_exists(tmp_path, monkeypatch):
    model_file = tmp_path / "model.json"
    model_file.write_text("{}")
    monkeypatch.setattr("src.ml.signal_combiner.MODEL_FILE", model_file)
    monkeypatch.setattr("src.ml.signal_combiner.STATUS_FILE", tmp_path / "missing_status.json")
    status = get_status(_fake_journal(40))
    assert status["active"] is True
    assert status["progress_pct"] == 100
    assert status["advisory_only"] is True


def test_active_label_includes_val_accuracy_when_available(tmp_path, monkeypatch):
    import json

    model_file = tmp_path / "model.json"
    model_file.write_text("{}")
    status_file = tmp_path / "status.json"
    status_file.write_text(json.dumps({"val_accuracy": 62.5}))
    monkeypatch.setattr("src.ml.signal_combiner.MODEL_FILE", model_file)
    monkeypatch.setattr("src.ml.signal_combiner.STATUS_FILE", status_file)
    status = get_status(_fake_journal(40))
    assert "62" in status["label"]
