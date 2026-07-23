"""LESSONS.md auto-generation — sections present, sane recommendations."""
from __future__ import annotations

from src.memory.lessons_writer import LessonsWriter


def test_write_creates_file(tmp_path, monkeypatch):
    out = tmp_path / "LESSONS.md"
    monkeypatch.setattr(LessonsWriter, "OUTPUT_FILE", out)
    writer = LessonsWriter()
    monkeypatch.setattr(
        "src.memory.journal.TradingJournal.get_active_knowledge", lambda self, **k: [])
    path = writer.write()
    assert path == out
    assert out.exists()


def test_write_has_sections(tmp_path, monkeypatch):
    out = tmp_path / "LESSONS.md"
    monkeypatch.setattr(LessonsWriter, "OUTPUT_FILE", out)
    monkeypatch.setattr(
        "src.memory.journal.TradingJournal.get_active_knowledge", lambda self, **k: [])
    LessonsWriter().write()
    content = out.read_text()
    assert "## System Performance Summary" in content
    assert "## What Works" in content
    assert "## Recommendations" in content


def test_recommendations_no_trades(tmp_path, monkeypatch):
    out = tmp_path / "LESSONS.md"
    monkeypatch.setattr(LessonsWriter, "OUTPUT_FILE", out)
    monkeypatch.setattr(
        "src.memory.journal.TradingJournal.get_active_knowledge", lambda self, **k: [])
    monkeypatch.setattr(
        "src.analytics.performance.PerformanceAnalyzer.get_summary",
        lambda self: {"total_trades": 0, "win_rate": 0, "total_pnl_inr": 0})
    LessonsWriter().write()
    content = out.read_text()
    assert "No real pipeline trades yet" in content
