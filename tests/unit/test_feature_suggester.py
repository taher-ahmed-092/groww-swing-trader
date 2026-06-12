"""Feature suggester — seed path (no API key in test env)."""
from __future__ import annotations

from src.agents.meta.feature_suggester import FeatureSuggester
from src.memory.journal import TradingJournal


def test_seed_suggestions_always_returns_3(tmp_path):
    suggester = FeatureSuggester(journal=TradingJournal(db_path=str(tmp_path / "fs.db")))
    out = suggester.run()
    assert isinstance(out, list)
    assert len(out) == 3


def test_seed_format_correct(tmp_path):
    suggester = FeatureSuggester(journal=TradingJournal(db_path=str(tmp_path / "fs.db")))
    for s in suggester.run():
        for key in ("title", "description", "effort", "expected_impact"):
            assert key in s
        assert s["effort"] in ("LOW", "MED", "HIGH")
