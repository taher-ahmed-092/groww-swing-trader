"""The "Learning phase: X/15" message was gated on the CURRENT regime's own
evidence count. That count is genuinely and correctly zero/low for a regime
that simply hasn't recurred much yet, even while other regimes (e.g. VOLATILE
with 527 trades) already have adaptive thresholds active — so the message
read as "the system failed to learn" when it was actually "this regime is
rare." The message has been removed entirely rather than relabeled, since
per-regime evidence is not a meaningful proxy for system-wide learning state."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.notifications.command_handler import CommandHandler


def _handler():
    h = CommandHandler.__new__(CommandHandler)
    h.tg = MagicMock()
    return h


def test_no_learning_phase_message_when_regime_evidence_below_min():
    h = _handler()
    thresholds = {"VOLATILE": {"judge_min": 7.5, "evidence": 3, "win_rate": 0.4}}
    tip = h._generate_improvement_tip(
        {"total_trades": 100}, {"total": 0}, 50, "VOLATILE", thresholds=thresholds)
    assert "Learning phase" not in tip
    assert "3/15" not in tip


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
