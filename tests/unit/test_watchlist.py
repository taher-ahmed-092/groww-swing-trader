"""Expanded watchlist — tiers, counts, tier-based risk params."""
from __future__ import annotations

from src.data.watchlist import ALL_STOCKS, get_risk_params_for_tier, get_tier


def test_all_stocks_have_tier():
    valid = {"large", "mid", "small"}
    for sym, d in ALL_STOCKS.items():
        assert d.get("tier") in valid, f"{sym} has bad tier {d.get('tier')}"
        assert d.get("sector"), f"{sym} missing sector"


def test_total_stock_count():
    assert len(ALL_STOCKS) >= 150


def test_liquidity_params_by_tier():
    large = get_risk_params_for_tier("large")
    small = get_risk_params_for_tier("small")
    # Small-caps take smaller positions and use tighter stops than large-caps.
    assert small["position_size_multiplier"] < large["position_size_multiplier"]
    assert small["min_volume_ratio"] > large["min_volume_ratio"]


def test_get_tier_unknown_defaults_large():
    assert get_tier("THIS_IS_NOT_A_REAL_SYMBOL") == "large"
