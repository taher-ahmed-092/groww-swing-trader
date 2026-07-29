"""Technical agent target-setting: was a FIXED reward ratio applied to the
ATR stop distance (neither resistance-based nor cost-aware) — a tight ATR
stop times a flat 2:1 ratio can produce a target whose net move barely
clears round-trip costs. Now a cost-aware floor (max(configured ratio,
8x round-trip cost as a fraction of stop distance)) applies, capped by
nearby resistance (R1) with a TARGET_CAPPED_BY_RESISTANCE flag."""
from __future__ import annotations

from config.risk_limits import LIMITS
from src.agents.technical.agent import TechnicalAgent


def test_cost_aware_floor_raises_target_for_tight_atr_stop():
    agent = TechnicalAgent()
    entry = 1000.0
    # Very tight ATR stop (0.3% away) — a flat 2:1 ratio would produce a
    # tiny target that can't plausibly clear round-trip costs.
    indicators = {"atr_stop": entry * 0.997}
    stop, target, flags = agent._levels(entry, indicators, tier="large")
    fixed_ratio_target = entry + (entry - stop) * LIMITS.target_reward_ratio
    assert target > fixed_ratio_target
    assert "TARGET_CAPPED_BY_RESISTANCE" not in flags


def test_resistance_below_cost_aware_target_caps_and_flags():
    agent = TechnicalAgent()
    entry = 1000.0
    indicators = {"atr_stop": entry * 0.997, "r1": entry * 1.01}
    stop, target, flags = agent._levels(entry, indicators, tier="large")
    assert target == round(entry * 1.01, 4)
    assert "TARGET_CAPPED_BY_RESISTANCE" in flags


def test_normal_stop_distance_uses_configured_ratio_floor():
    agent = TechnicalAgent()
    entry = 1000.0
    # A stop distance wide enough that the configured ratio already clears
    # costs comfortably — cost-aware ratio should not exceed the floor.
    indicators = {"atr_stop": entry * 0.93}
    stop, target, flags = agent._levels(entry, indicators, tier="large")
    expected = round(entry + (entry - stop) * LIMITS.target_reward_ratio, 4)
    assert target == expected
