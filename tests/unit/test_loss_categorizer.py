"""Loss categorizer: PRE-SNAPSHOT bucket is distinct from UNKNOWN.

Fix 4: a trade with no entry_snapshot at all (predates the field) has zero
data to categorize from — that's fundamentally different from a trade that
HAS a snapshot but matched none of the known patterns (genuine UNKNOWN).
Conflating them made "UNKNOWN: 328" look like an analysis gap when it was
mostly just old trades.
"""
from __future__ import annotations

from src.analytics.loss_categorizer import (
    PRE_SNAPSHOT_LABEL,
    UNCATEGORISED_LABEL,
    breakdown,
    breakdown_for_chart,
    categorize_loss,
)


def test_missing_entry_snapshot_is_pre_snapshot_not_unknown():
    assert categorize_loss({"entry": 100, "exit": 95}, None) == PRE_SNAPSHOT_LABEL


def test_snapshot_with_no_indicators_is_pre_snapshot():
    assert categorize_loss({"entry": 100, "exit": 95}, {"indicators": {}}) == PRE_SNAPSHOT_LABEL


def test_snapshot_present_but_no_rule_matches_is_uncategorised():
    trade = {"entry": 100, "stop": 85, "exit": 96}
    snapshot = {"indicators": {"atr_14": 10, "rsi_14": 50, "adx_signal": "TRENDING"},
                "market_context": {"regime": "BULL_TRENDING"}}
    assert categorize_loss(trade, snapshot) == UNCATEGORISED_LABEL


def test_full_snapshot_never_lands_in_pre_snapshot():
    """A trade with a full entry_snapshot must never be categorized as
    PRE_SNAPSHOT — that bucket is reserved for trades with no snapshot data
    at all (predating the field), not for genuinely uncategorizable ones."""
    trade = {"entry": 100, "stop": 85, "exit": 96}
    snapshot = {"indicators": {"atr_14": 10, "rsi_14": 50, "adx_signal": "TRENDING"},
                "market_context": {"regime": "BULL_TRENDING"}}
    assert categorize_loss(trade, snapshot) != PRE_SNAPSHOT_LABEL


def test_breakdown_for_chart_excludes_pre_snapshot():
    trades = [
        ({"entry": 100, "stop": 99, "exit": 99}, None),  # PRE_SNAPSHOT
        ({"entry": 100, "stop": 85, "exit": 96},
         {"indicators": {"atr_14": 10, "rsi_14": 50, "adx_signal": "TRENDING"},
          "market_context": {"regime": "BULL_TRENDING"}}),  # UNCATEGORISED
    ]
    counts, pre_snapshot_count = breakdown_for_chart(trades)
    assert PRE_SNAPSHOT_LABEL not in counts
    assert pre_snapshot_count == 1
    assert counts[UNCATEGORISED_LABEL] == 1


def test_stop_too_tight_detected_with_snapshot():
    trade = {"entry": 100, "stop": 99, "exit": 99}
    snapshot = {"indicators": {"atr_14": 5}}
    assert categorize_loss(trade, snapshot) == "STOP_TOO_TIGHT"


def test_breakdown_separates_pre_snapshot_from_categorized():
    trades = [
        ({"entry": 100, "stop": 99, "exit": 99}, None),  # PRE_SNAPSHOT
        ({"entry": 100, "stop": 99, "exit": 99}, None),  # PRE_SNAPSHOT
        ({"entry": 100, "stop": 85, "exit": 96},
         {"indicators": {"atr_14": 10, "rsi_14": 50, "adx_signal": "TRENDING"},
          "market_context": {"regime": "BULL_TRENDING"}}),  # UNKNOWN
    ]
    counts = breakdown(trades)
    assert counts[PRE_SNAPSHOT_LABEL] == 2
    assert counts[UNCATEGORISED_LABEL] == 1
