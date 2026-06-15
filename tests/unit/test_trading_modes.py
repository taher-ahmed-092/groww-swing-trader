"""Trading modes — conserve / balanced / rogue risk dial."""
from __future__ import annotations

from src.trading.modes import MODES, get_mode_config


def test_conserve_higher_threshold():
    # Stricter modes demand a higher judge bar: conserve > balanced > rogue.
    assert (MODES["conserve"].judge_threshold
            > MODES["balanced"].judge_threshold
            > MODES["rogue"].judge_threshold)


def test_rogue_most_permissive():
    lowest = min(m.judge_threshold for m in MODES.values())
    assert MODES["rogue"].judge_threshold == lowest
    assert MODES["rogue"].trades_downtrends is True


def test_mode_config_complete():
    required = ("name", "emoji", "judge_threshold", "max_trades_per_week",
               "position_size_multiplier", "trades_downtrends", "description")
    for name, cfg in MODES.items():
        for field in required:
            assert getattr(cfg, field, None) is not None, f"{name} missing {field}"


def test_unknown_mode_defaults_balanced():
    assert get_mode_config("nonsense").name == "balanced"
    assert get_mode_config(None).name == "balanced"
