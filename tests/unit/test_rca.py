"""Root Cause Analysis behavior (heuristic path — no API key needed)."""
from __future__ import annotations

from src.memory.journal import KnowledgeEntry, TradeRecord, TradingJournal
from src.memory.rca import RootCauseAnalyzer


def _journal(tmp_path):
    return TradingJournal(db_path=str(tmp_path / "rca.db"))


def _loss_trade(**kw):
    base = dict(id=1, symbol="HDFCBANK", sector="Banking", pnl_pct=-3.0, outcome="LOSS")
    base.update(kw)
    return TradeRecord(**base)


def test_rca_categorizes_market_event(tmp_path):
    analyzer = RootCauseAnalyzer(_journal(tmp_path))
    snapshot = {"market_context": {"nifty_trend": "DOWNTREND"}, "sector": "Banking"}
    rec = analyzer.analyze(_loss_trade(), snapshot)
    assert rec.failure_category == "MARKET_REGIME"


def test_rca_categorizes_overconfidence(tmp_path):
    analyzer = RootCauseAnalyzer(_journal(tmp_path))
    snapshot = {
        "market_context": {"nifty_trend": "UPTREND"},
        "manually_requested": True,
        "judge_verdict": {"flags": []},
        "sector": "Banking",
    }
    rec = analyzer.analyze(_loss_trade(), snapshot)
    assert rec.failure_category == "OVERCONFIDENCE"


def test_noise_loss_skips_knowledge_update(tmp_path):
    # MARKET_REGIME (DOWNTREND) is noise — it must NOT create a knowledge pattern.
    journal = _journal(tmp_path)
    RootCauseAnalyzer(journal).analyze(
        _loss_trade(), {"market_context": {"nifty_trend": "DOWNTREND"}, "sector": "Banking"}
    )
    assert len(journal.get_active_knowledge(min_confidence=0.0)) == 0


def test_signal_loss_creates_knowledge_entry(tmp_path):
    # A genuine signal failure (OVERCONFIDENCE via manual request) DOES create one.
    journal = _journal(tmp_path)
    snapshot = {
        "market_context": {"nifty_trend": "UPTREND"},
        "manually_requested": True,
        "judge_verdict": {"flags": []},
        "sector": "Banking",
    }
    RootCauseAnalyzer(journal).analyze(_loss_trade(), snapshot)
    assert len(journal.get_active_knowledge(min_confidence=0.0)) == 1


def test_rca_win_updates_confidence(tmp_path):
    journal = _journal(tmp_path)
    journal.log_knowledge_entry(
        KnowledgeEntry(
            pattern_id="banking-overbought-fails",
            pattern_description="Banking sector overbought fails",
            category="SECTOR_PATTERN",
            confidence=0.1,
            observed_count=1,
        )
    )
    analyzer = RootCauseAnalyzer(journal)
    win = TradeRecord(id=2, symbol="ICICIBANK", sector="Banking", pnl_pct=5.0, outcome="WIN")
    analyzer.analyze_win(win, {"sector": "Banking"})
    updated = journal.get_active_knowledge(min_confidence=0.0)[0]
    assert updated.confidence > 0.1
    assert updated.observed_count == 2
