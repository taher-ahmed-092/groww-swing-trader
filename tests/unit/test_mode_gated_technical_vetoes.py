"""Rogue mode skips SUPERTREND_BEARISH / SELLING_PRESSURE / BELOW_ALL_SUPPORTS
auto-vetoes; balanced/conserve keep them active. POOR_RISK_REWARD is never
gated by mode."""
from __future__ import annotations

from unittest.mock import patch

from src.judge.evaluator import LLMJudge


def _bearish_technical():
    return {
        "signal": "BUY",
        "indicators": {"supertrend_direction": "BEARISH", "adx_signal": "NEUTRAL"},
    }


@patch("src.judge.evaluator.get_current_mode")
def test_rogue_mode_skips_supertrend_bearish_veto(mock_mode):
    from src.trading.modes import MODES

    mock_mode.return_value = MODES["rogue"]
    judge = LLMJudge()
    flags = judge._auto_veto({}, _bearish_technical(), {}, rr=2.0)
    assert "SUPERTREND_BEARISH" not in flags


@patch("src.judge.evaluator.get_current_mode")
def test_balanced_mode_still_vetoes_supertrend_bearish(mock_mode):
    from src.trading.modes import MODES

    mock_mode.return_value = MODES["balanced"]
    judge = LLMJudge()
    flags = judge._auto_veto({}, _bearish_technical(), {}, rr=2.0)
    assert "SUPERTREND_BEARISH" in flags


@patch("src.judge.evaluator.get_current_mode")
def test_rogue_mode_still_vetoes_poor_risk_reward(mock_mode):
    from src.trading.modes import MODES

    mock_mode.return_value = MODES["rogue"]
    judge = LLMJudge()
    flags = judge._auto_veto({}, _bearish_technical(), {}, rr=1.0)
    assert "POOR_RISK_REWARD" in flags
