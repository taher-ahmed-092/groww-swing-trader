"""Sector diversification cap in the risk checker."""
from __future__ import annotations

from config.risk_limits import LIMITS
from src.memory.journal import TradeRecord, TradingJournal
from src.orchestrator.state import get_initial_state
from src.risk.checker import RiskChecker


def _approved_state(sector):
    s = get_initial_state("NEWBANK")
    s["sector"] = sector
    s["fundamental_verdict"] = {"score": 0.85}
    s["technical_verdict"] = {"score": 0.85, "signal": "BUY", "entry_price": 100.0,
                              "stop_price": 93.0, "target_price": 114.0}
    s["judge_verdict"] = {"approved": True, "overall_score": 8.0, "flags": []}
    s["gut_check"] = {"gut_score": 7.0}
    return s


def _journal_with_open(tmp_path, sector, n):
    journal = TradingJournal(db_path=str(tmp_path / "sec.db"))
    from sqlmodel import Session
    with Session(journal.engine) as ss:
        for i in range(n):
            ss.add(TradeRecord(symbol=f"{sector}{i}", sector=sector, outcome="OPEN",
                               entry_price=100.0, stop_price=93.0, target_price=114.0))
        ss.commit()
    return journal


def test_sector_cap_blocks_third(tmp_path):
    journal = _journal_with_open(tmp_path, "Banking", LIMITS.max_positions_per_sector)
    result = RiskChecker(journal=journal).check(_approved_state("Banking"))
    assert result["approved"] is False
    assert any("sector cap" in r.lower() for r in result["reasons"])


def test_sector_cap_allows_different(tmp_path):
    journal = _journal_with_open(tmp_path, "Banking", LIMITS.max_positions_per_sector)
    result = RiskChecker(journal=journal).check(_approved_state("IT"))
    assert result["approved"] is True
