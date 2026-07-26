"""Tests for scripts/validate_watchlist.py's dead-ticker diff/persistence logic."""
from __future__ import annotations

import json
from unittest.mock import patch

from scripts import validate_watchlist


def test_first_run_has_no_baseline_to_diff(tmp_path, monkeypatch):
    dead_file = tmp_path / "dead_tickers.json"
    monkeypatch.setattr(validate_watchlist, "DEAD_TICKERS_FILE", dead_file)

    with patch.object(validate_watchlist, "validate", return_value=["FOO", "BAR"]):
        new_dead = validate_watchlist.validate_and_report_new_dead_tickers()

    assert new_dead == []
    assert json.loads(dead_file.read_text()) == ["BAR", "FOO"]


def test_only_newly_dead_tickers_are_reported(tmp_path, monkeypatch):
    dead_file = tmp_path / "dead_tickers.json"
    dead_file.write_text(json.dumps(["FOO"]))
    monkeypatch.setattr(validate_watchlist, "DEAD_TICKERS_FILE", dead_file)

    with patch.object(validate_watchlist, "validate", return_value=["FOO", "BAZ"]):
        new_dead = validate_watchlist.validate_and_report_new_dead_tickers()

    assert new_dead == ["BAZ"]
    assert json.loads(dead_file.read_text()) == ["BAZ", "FOO"]


def test_no_newly_dead_tickers_returns_empty(tmp_path, monkeypatch):
    dead_file = tmp_path / "dead_tickers.json"
    dead_file.write_text(json.dumps(["FOO", "BAR"]))
    monkeypatch.setattr(validate_watchlist, "DEAD_TICKERS_FILE", dead_file)

    with patch.object(validate_watchlist, "validate", return_value=["FOO"]):
        new_dead = validate_watchlist.validate_and_report_new_dead_tickers()

    assert new_dead == []
