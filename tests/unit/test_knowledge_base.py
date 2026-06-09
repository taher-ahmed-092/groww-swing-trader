"""KnowledgeBase confidence/hypothesis mechanics."""
from __future__ import annotations

from src.memory.journal import KnowledgeEntry, TradingJournal


def _journal(tmp_path):
    return TradingJournal(db_path=str(tmp_path / "kb.db"))


def _entry(pattern_id="p", confidence=0.1, observed_count=1):
    return KnowledgeEntry(
        pattern_id=pattern_id,
        pattern_description=f"desc-{pattern_id}",
        category="SECTOR_PATTERN",
        confidence=confidence,
        observed_count=observed_count,
        is_hypothesis=observed_count < 3,
    )


def test_hypothesis_flag(tmp_path):
    journal = _journal(tmp_path)
    saved = journal.log_knowledge_entry(_entry(observed_count=1))
    assert saved.is_hypothesis is True


def test_graduates_at_3_observations(tmp_path):
    journal = _journal(tmp_path)
    journal.log_knowledge_entry(_entry(pattern_id="grad", observed_count=1))
    for _ in range(3):
        journal.update_knowledge_confidence("grad", confirmed=True)
    updated = next(e for e in journal.get_active_knowledge(0.0) if e.pattern_id == "grad")
    assert updated.is_hypothesis is False
    assert updated.observed_count == 4


def test_confidence_increases_on_confirm(tmp_path):
    journal = _journal(tmp_path)
    journal.log_knowledge_entry(_entry(pattern_id="up", confidence=0.1))
    for _ in range(3):
        journal.update_knowledge_confidence("up", confirmed=True)
    updated = next(e for e in journal.get_active_knowledge(0.0) if e.pattern_id == "up")
    assert abs(updated.confidence - 0.4) < 1e-9


def test_confidence_decreases_on_contradict(tmp_path):
    journal = _journal(tmp_path)
    journal.log_knowledge_entry(_entry(pattern_id="down", confidence=0.5))
    journal.update_knowledge_confidence("down", confirmed=False)
    updated = next(e for e in journal.get_active_knowledge(0.0) if e.pattern_id == "down")
    assert abs(updated.confidence - 0.45) < 1e-9


def test_format_filters_by_min_confidence(tmp_path):
    journal = _journal(tmp_path)
    journal.log_knowledge_entry(_entry(pattern_id="high", confidence=0.5))
    journal.log_knowledge_entry(_entry(pattern_id="low", confidence=0.2))
    out = journal.format_knowledge_for_context(min_confidence=0.3)
    assert "desc-high" in out
    assert "desc-low" not in out
