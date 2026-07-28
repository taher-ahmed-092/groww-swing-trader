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
    breakdown,
    categorize_loss,
)


def test_missing_entry_snapshot_is_pre_snapshot_not_unknown():
    assert categorize_loss({"entry": 100, "exit": 95}, None) == PRE_SNAPSHOT_LABEL


def test_snapshot_with_no_indicators_is_pre_snapshot():
    assert categorize_loss({"entry": 100, "exit": 95}, {"indicators": {}}) == PRE_SNAPSHOT_LABEL


def test_snapshot_present_but_no_rule_matches_is_unknown():
    trade = {"entry": 100, "stop": 85, "exit": 96}
    snapshot = {"indicators": {"atr_14": 10, "rsi_14": 50, "adx_signal": "TRENDING"},
                "market_context": {"regime": "BULL_TRENDING"}}
    assert categorize_loss(trade, snapshot) == "UNKNOWN"


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
    assert counts["UNKNOWN"] == 1
