"""Sector-aware fundamental hard rejects (screener.check_hard_rejects).

Root cause of 0 real trades: sector-blind rules disqualified most of the
market — ROCE<10 rejects every bank/NBFC (deposits sit in capital employed,
so ROCE isn't a valid metric for financials), and promoter_holding<25%
rejected professionally-managed blue chips (HDFC Bank, ICICI, L&T, ITC,
Infosys are all near 0% promoter holding — that's institutional ownership,
not a risk).
"""
from __future__ import annotations

from src.data.screener import ScreenerScraper


def _scraper():
    return ScreenerScraper()


def test_bank_with_moderate_roce_and_healthy_roe_not_rejected():
    """AXISBANK-like: ROCE 6.24% (would fail the old <10 floor) but ROE 15%
    (genuinely healthy for a bank) must NOT be hard-rejected."""
    data = {"roce_pct": 6.24, "roe_pct": 15.0, "debt_to_equity": 8.5}
    rejects = _scraper().check_hard_rejects(data, sector="Banking")
    assert rejects == []


def test_bank_with_poor_roe_is_rejected():
    data = {"roce_pct": 6.24, "roe_pct": 3.0}
    rejects = _scraper().check_hard_rejects(data, sector="Banking")
    assert any("ROE" in r for r in rejects)


def test_non_financial_with_roce_4_percent_is_rejected():
    """A non-financial with genuinely poor ROCE (4%, below the new 6% floor)
    must still be hard-rejected — the floor moved from 10 to 6, not removed."""
    data = {"roce_pct": 4.0}
    rejects = _scraper().check_hard_rejects(data, sector="IT")
    assert any("capital efficiency" in r.lower() for r in rejects)


def test_non_financial_with_roce_8_percent_not_rejected():
    """ROCE 8% is mediocre, not disqualifying — belongs in scoring, not a veto."""
    data = {"roce_pct": 8.0}
    rejects = _scraper().check_hard_rejects(data, sector="IT")
    assert rejects == []


def test_hdfc_like_zero_promoter_holding_not_rejected():
    """0% promoter holding + 0% pledging = professionally-managed/institutional
    company (HDFC Bank, ICICI, L&T, ITC, Infosys) — never a risk signal alone."""
    data = {"roce_pct": 20.0, "roe_pct": 18.0, "promoter_holding_pct": 0.0,
            "promoter_pledged_pct": 0.0}
    rejects = _scraper().check_hard_rejects(data, sector="Banking")
    assert rejects == []


def test_low_holding_with_pledging_is_rejected():
    """Low holding (1-25%) WITH pledging (>15%) is the real red flag."""
    data = {"roce_pct": 15.0, "promoter_holding_pct": 10.0, "promoter_pledged_pct": 20.0}
    rejects = _scraper().check_hard_rejects(data, sector="IT")
    assert any("promoter holding" in r.lower() for r in rejects)


def test_low_holding_without_pledging_not_rejected():
    data = {"roce_pct": 15.0, "promoter_holding_pct": 10.0, "promoter_pledged_pct": 2.0}
    rejects = _scraper().check_hard_rejects(data, sector="IT")
    assert rejects == []


def test_financial_sector_skips_debt_to_equity_rule():
    """Leverage is a bank's business model — D/E must never hard-reject a financial."""
    data = {"roce_pct": 6.24, "roe_pct": 15.0, "debt_to_equity": 9.5}
    rejects = _scraper().check_hard_rejects(data, sector="Finance")
    assert rejects == []


def test_non_financial_high_debt_still_rejected():
    data = {"roce_pct": 15.0, "debt_to_equity": 4.0}
    rejects = _scraper().check_hard_rejects(data, sector="Auto")
    assert any("debt" in r.lower() for r in rejects)


def test_no_sector_defaults_to_non_financial_rules():
    data = {"roce_pct": 4.0}
    rejects = _scraper().check_hard_rejects(data, sector=None)
    assert any("capital efficiency" in r.lower() for r in rejects)
