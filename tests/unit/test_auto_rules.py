"""Auto-rule extraction — LESSONS.md knowledge base -> actionable behavior."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.memory.auto_rules import AutoRuleExtractor


def _entry(desc, confidence, observed_count=15):
    e = MagicMock()
    e.pattern_description = desc
    e.confidence = confidence
    e.observed_count = observed_count
    return e


def _extractor_with_kb(kb, tmp_path, monkeypatch):
    monkeypatch.setattr("src.memory.auto_rules.AUTO_RULES_FILE", tmp_path / "rules.json")
    ex = AutoRuleExtractor()
    ex.journal = None
    monkeypatch.setattr(
        "src.memory.journal.TradingJournal.get_active_knowledge", lambda self, **k: kb)
    # Isolate from real data/dossiers/ — dossier-driven vetoes/boosts are
    # covered separately in test_dossier_auto_rules.py; these KB-only tests
    # must not be sensitive to whatever dossiers happen to exist on disk.
    monkeypatch.setattr("src.memory.company_dossier.dossier_store.search", lambda **k: [])
    return ex


def test_extract_runs_empty_kb(tmp_path, monkeypatch):
    ex = _extractor_with_kb([], tmp_path, monkeypatch)
    rules = ex.extract_and_save()
    assert rules["stock_vetoes"] == {}
    assert rules["stock_boosts"] == {}
    # Seed veto is always present.
    assert len(rules["setup_vetoes"]) == 1


def test_veto_extraction(tmp_path, monkeypatch):
    kb = [_entry("REPLAY LOST: RELIANCE (large) | RSI 56 | UPTREND | ADX CHOPPY", 0.9)]
    ex = _extractor_with_kb(kb, tmp_path, monkeypatch)
    rules = ex.extract_and_save()
    assert "RELIANCE" in rules["stock_vetoes"]


def test_boost_extraction(tmp_path, monkeypatch):
    kb = [_entry("REPLAY WON: RELIANCE (large) | RSI 56 | UPTREND | ADX TRENDING", 0.9)]
    ex = _extractor_with_kb(kb, tmp_path, monkeypatch)
    rules = ex.extract_and_save()
    assert "RELIANCE" in rules["stock_boosts"]


def test_seed_veto_always_present(tmp_path, monkeypatch):
    ex = _extractor_with_kb([], tmp_path, monkeypatch)
    rules = ex.extract_and_save()
    assert any(v.get("rsi_min") == 57 and v.get("adx_signal") == "CHOPPY"
               for v in rules["setup_vetoes"])


def test_scout_applies_veto(tmp_path, monkeypatch):
    monkeypatch.setattr("src.memory.auto_rules.AUTO_RULES_FILE", tmp_path / "rules.json")
    rules = {
        "stock_vetoes": {"RELIANCE": {"confidence": 0.9, "reason": "test"}},
        "stock_boosts": {}, "setup_vetoes": [], "setup_boosts": [],
    }
    (tmp_path / "rules.json").write_text(__import__("json").dumps(rules))
    loaded = AutoRuleExtractor.load()
    assert loaded["stock_vetoes"]["RELIANCE"]["confidence"] == 0.9
