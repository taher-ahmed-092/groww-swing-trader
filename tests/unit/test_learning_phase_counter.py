"""The "Learning phase: X/15" message was gated on forced.get("total", 0) —
TODAY's closed forced-trade count, which resets every midnight — so it read
"0/15" for weeks despite thousands of accumulated current-era simulation
trades and 80+ KB patterns. It must gate on the actual MIN_EVIDENCE (15)
per-regime cumulative evidence count that AdaptiveThresholds itself uses."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.notifications.command_handler import CommandHandler


def _handler():
    h = CommandHandler.__new__(CommandHandler)
    h.tg = MagicMock()
    return h


def test_learning_phase_shown_when_regime_evidence_below_min():
    h = _handler()
    thresholds = {"VOLATILE": {"judge_min": 7.5, "evidence": 3, "win_rate": 0.4}}
    # forced["total"] is deliberately 0 (today reset) to prove the message
    # no longer depends on it.
    tip = h._generate_improvement_tip(
        {"total_trades": 100}, {"total": 0}, 50, "VOLATILE", thresholds=thresholds)
    assert "Learning phase" in tip
    assert "3/15" in tip


def test_adaptive_threshold_shown_once_regime_evidence_clears_min():
    h = _handler()
    thresholds = {"VOLATILE": {"judge_min": 7.5, "evidence": 527, "win_rate": 0.45}}
    tip = h._generate_improvement_tip(
        {"total_trades": 100}, {"total": 0}, 50, "VOLATILE", thresholds=thresholds)
    assert "Learning phase" not in tip
    assert "Adaptive threshold (VOLATILE)" in tip
    assert "527 trades" in tip


def test_today_reset_forced_total_does_not_trigger_stale_learning_phase():
    """Regression: forced["total"]=0 (a fresh day) must not force the
    learning-phase message once regime evidence has already cleared MIN_EVIDENCE —
    this was the exact stuck "0/15" bug."""
    h = _handler()
    thresholds = {"BULL_TRENDING": {"judge_min": 6.0, "evidence": 200, "win_rate": 0.55}}
    tip = h._generate_improvement_tip(
        {"total_trades": 50}, {"total": 0}, 60, "BULL_TRENDING", thresholds=thresholds)
    assert "0/15" not in tip
