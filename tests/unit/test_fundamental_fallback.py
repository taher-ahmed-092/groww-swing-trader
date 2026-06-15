"""Demo-mode fundamental scorer must never hard-zero on missing data."""
from __future__ import annotations

from src.agents.fundamental.rule_score import rule_based_fundamental_score


def test_empty_data_returns_neutral():
    out = rule_based_fundamental_score({}, {})
    assert out["score"] == 0.50
    assert out["data_missing"] is True


def test_never_returns_zero():
    all_none = {
        "roce_pct": None, "debt_to_equity": None, "sales_growth_3yr": None,
        "profit_growth_3yr": None, "promoter_holding_pct": None,
        "promoter_pledged_pct": None, "pe_ratio": None, "piotroski_score": None,
        "fcf_yield": None,
    }
    out = rule_based_fundamental_score(all_none, {})
    assert out["score"] >= 0.40


def test_present_but_weak_still_scores_low_not_missing():
    # Real (present) weak data should score low and NOT be flagged data_missing.
    weak = {"roce_pct": 2.0, "debt_to_equity": 3.0, "pe_ratio": 90.0}
    out = rule_based_fundamental_score(weak, {})
    assert out["data_missing"] is False
    assert out["score"] < 0.50
