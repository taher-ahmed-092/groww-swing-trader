"""Batch API cache behavior (no network — cache file logic only)."""
from __future__ import annotations

import json
import os
import time

from src.batch.weekly_research_batch import WeeklyResearchBatch


def test_get_batch_fundamental_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr("src.batch.weekly_research_batch._RESULTS", str(tmp_path / "none.json"))
    assert WeeklyResearchBatch().get_batch_fundamental("RELIANCE") is None


def test_get_batch_fundamental_stale(tmp_path, monkeypatch):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({"RELIANCE": {"score": 0.8}}))
    old = time.time() - 40 * 3600  # 40h ago > 36h TTL
    os.utime(path, (old, old))
    monkeypatch.setattr("src.batch.weekly_research_batch._RESULTS", str(path))
    assert WeeklyResearchBatch().get_batch_fundamental("RELIANCE") is None


def test_get_batch_fundamental_fresh(tmp_path, monkeypatch):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({"RELIANCE": {"score": 0.8}}))
    monkeypatch.setattr("src.batch.weekly_research_batch._RESULTS", str(path))
    out = WeeklyResearchBatch().get_batch_fundamental("RELIANCE")
    assert out == {"score": 0.8}
