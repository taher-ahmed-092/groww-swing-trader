"""Pairs judge scoring: z=2.30/correlation=0.80 must score well above 5, not
0.0. Root cause of the 0.0 production bug was upstream (market_open_pairs_job
never populated state["pairs_opportunity"], so technical_node discarded the
pre-built pairs verdict and re-ran ordinary momentum analysis) — this test
covers the scoring function itself plus its exception safety net."""
from __future__ import annotations

from src.judge.evaluator import LLMJudge


def _pairs_state(z_score, correlation):
    return {"pairs_opportunity": {
        "z_score": z_score, "correlation": correlation,
        "buy_symbol": "A", "pair_symbol": "B",
    }}


def test_z2_3_corr_0_8_scores_above_5():
    judge = LLMJudge()
    verdict = judge._evaluate_pairs_trade(_pairs_state(2.30, 0.80), {"strategy_name": "pairs_trading"})
    assert verdict["overall_score"] > 5
    assert 6.5 <= verdict["overall_score"] <= 7.5
    assert verdict["approved"] is True


def test_scoring_exception_is_caught_and_logged(capsys):
    judge = LLMJudge()
    # A non-numeric z_score/correlation would otherwise raise inside the
    # arithmetic — must be caught, logged, and degrade to a safe score
    # rather than propagating or silently producing a wrong value.
    verdict = judge._evaluate_pairs_trade(
        _pairs_state("not-a-number", 0.80), {"strategy_name": "pairs_trading"})
    assert verdict["overall_score"] == 0.0
    out = capsys.readouterr().out
    assert "Pairs scoring math failed" in out


def test_pairs_opportunity_wired_into_market_open_pairs_job():
    """Regression guard for the actual root cause: runner.py must pass the
    pairs opportunity through state so technical_node's StrategyRegistry call
    re-fires the pairs strategy (preserving strategy_name) instead of falling
    through to ordinary momentum analysis on the buy leg."""
    src = open("runner.py", encoding="utf-8").read()
    idx = src.find("def market_open_pairs_job")
    idx_end = src.find("def market_open_scan_job")
    body = src[idx:idx_end]
    assert 'state["pairs_opportunity"] = best' in body
