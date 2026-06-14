"""Piotroski F-Score + FCF yield in fundamental ratios."""
from __future__ import annotations

from src.agents.fundamental.ratios import compute_ratios, piotroski_f_score

_ALL_POSITIVE = {
    "returnOnAssets": 0.10, "returnOnAssets_prev": 0.05,   # F1, F3
    "operatingCashflow": 100, "netIncome": 50,             # F2, F4
    "longTermDebt": 50, "longTermDebt_prev": 100,          # F5
    "currentRatio": 2.0, "currentRatio_prev": 1.5,         # F6
    "sharesOutstanding": 100, "sharesOutstanding_prev": 100,  # F7
    "grossMargins": 0.40, "grossMargins_prev": 0.30,       # F8
    "totalRevenue": 1000, "totalAssets": 500,
    "totalRevenue_prev": 900, "totalAssets_prev": 500,     # F9
}

_ALL_NEGATIVE = {
    "returnOnAssets": -0.10, "returnOnAssets_prev": 0.20,
    "operatingCashflow": -10, "netIncome": 5,
    "longTermDebt": 100, "longTermDebt_prev": 50,
    "currentRatio": 1.0, "currentRatio_prev": 2.0,
    "sharesOutstanding": 200, "sharesOutstanding_prev": 100,
    "grossMargins": 0.20, "grossMargins_prev": 0.40,
    "totalRevenue": 900, "totalAssets": 600,
    "totalRevenue_prev": 1000, "totalAssets_prev": 500,
}


def test_piotroski_all_points():
    assert piotroski_f_score(_ALL_POSITIVE) == 9


def test_piotroski_no_points():
    assert piotroski_f_score(_ALL_NEGATIVE) == 0


def test_piotroski_label_strong():
    # Drop one point (F7 dilution) → 8 → STRONG.
    info = dict(_ALL_POSITIVE, sharesOutstanding=200)
    out = compute_ratios(info)
    assert out["piotroski_score"] == 8
    assert out["piotroski_label"] == "STRONG"


def test_piotroski_label_weak():
    # Only F1 + F2 true → 2 → WEAK.
    info = {"returnOnAssets": 0.1, "operatingCashflow": 100, "netIncome": 200}
    out = compute_ratios(info)
    assert out["piotroski_score"] <= 3
    assert out["piotroski_label"] == "WEAK"


def test_piotroski_missing_data():
    score = piotroski_f_score({})
    assert 0 <= score <= 9
    assert compute_ratios({})["piotroski_label"] in ("STRONG", "NEUTRAL", "WEAK")


def test_fcf_yield_computed():
    out = compute_ratios({"freeCashflow": 100, "marketCap": 1000})
    assert out["fcf_yield"] == 0.1
    assert out["fcf_yield_label"] == "VERY_ATTRACTIVE"
