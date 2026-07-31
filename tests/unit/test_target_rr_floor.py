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


def test_resistance_too_close_to_entry_is_ignored_as_noise():
    """R1 = 2*pivot - prior_low routinely lands 0.5-1% above entry for a
    stock near its recent high — relative to a 7% stop that's noise, not a
    real target, and was capping the target so tightly that judge fired
    POOR_RISK_REWARD on nearly every candidate. Below
    min_resistance_distance_ratio (1.5x stop distance), R1 must be ignored
    and the cost-aware target kept."""
    agent = TechnicalAgent()
    entry = 1000.0
    indicators = {"atr_stop": entry * 0.93, "r1": entry * 1.005}
    stop, target, flags = agent._levels(entry, indicators, tier="large")
    cost_aware_target = round(entry + (entry - stop) * LIMITS.target_reward_ratio, 4)
    assert target == cost_aware_target
    assert "TARGET_CAPPED_BY_RESISTANCE" not in flags


def test_resistance_far_enough_away_is_meaningful_and_caps_target():
    """Once R1 clears the 1.5x-stop-distance floor (and still sits below the
    cost-aware target), it IS a meaningful nearby resistance level and the
    target is capped there — resistance-capping isn't removed, just gated
    on distance. (entry*1.12, not the illustrative entry*1.15 from the spec:
    with this stop distance the cost-aware target itself is entry*1.14, so
    entry*1.15 resistance would sit ABOVE it and never engage the cap at
    all — entry*1.12 is the smallest round value that both clears the 1.5x
    floor (>= entry*1.105) and stays below the cost-aware target.)"""
    agent = TechnicalAgent()
    entry = 1000.0
    indicators = {"atr_stop": entry * 0.93, "r1": entry * 1.12}
    stop, target, flags = agent._levels(entry, indicators, tier="large")
    assert target == round(entry * 1.12, 4)
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
