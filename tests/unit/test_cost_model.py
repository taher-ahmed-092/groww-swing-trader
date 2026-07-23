"""Indian equity transaction cost model."""
from __future__ import annotations

from src.trading.cost_model import compute_round_trip_costs, net_pnl_pct


def test_delivery_costs_reasonable():
    costs = compute_round_trip_costs(1000, 1010, 1, "large", is_intraday=False)
    assert 0.1 <= costs.total_pct <= 0.6


def test_intraday_cheaper_stt():
    delivery = compute_round_trip_costs(1000, 1010, 1, "large", is_intraday=False)
    intraday = compute_round_trip_costs(1000, 1010, 1, "large", is_intraday=True)
    assert intraday.stt < delivery.stt


def test_small_cap_higher_slippage():
    large = compute_round_trip_costs(1000, 1010, 1, "large")
    small = compute_round_trip_costs(1000, 1010, 1, "small")
    assert small.slippage > large.slippage


def test_net_pnl_reduces_gross():
    gross = 2.0
    net = net_pnl_pct(gross, 1000, 1020, "large", is_intraday=True)
    assert net < gross
