"""WIN/LOSS icon logic — LOST/LOSS checked before WON/WIN (audit bug: a
'CONT-SIM WIN' description rendered as a loss because only 'WON' was checked)."""
from __future__ import annotations

from src.memory.lessons_writer import _icon_for


def test_won_shows_win_icon():
    assert _icon_for("REPLAY ✅ WON: ONGC (large) | RSI 51 | DOWNTREND") == "✅"


def test_lost_shows_loss_icon():
    assert _icon_for("REPLAY ❌ LOST: BIOCON (mid) | RSI 56 | UPTREND") == "❌"


def test_cont_sim_win_shows_win_icon():
    assert _icon_for("CONT-SIM WIN: large | RSI mid | DOWNTREND | ADX TRENDING") == "✅"


def test_cont_sim_loss_shows_loss_icon():
    assert _icon_for("CONT-SIM LOSS: large | RSI mid | DOWNTREND | ADX NEUTRAL") == "❌"


def test_forced_outcome_win_shows_win_icon():
    assert _icon_for("FORCED OUTCOME: high signal (0.80) -> WIN (+4.8%)") == "✅"


def test_forced_outcome_loss_shows_loss_icon():
    assert _icon_for("FORCED OUTCOME: mid signal (0.50) -> LOSS (-3.3%)") == "❌"


def test_neutral_description_shows_diamond():
    assert _icon_for("TITAN BUY signal worked in RECOVERY (RSI 39.9, SIDEWAYS)") == "◆"
