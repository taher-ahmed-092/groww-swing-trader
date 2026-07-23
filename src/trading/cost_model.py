"""
Indian equity delivery/intraday transaction cost model.

Costs per side (approximate, Groww discount broker):
- Brokerage: zero for equity delivery on Groww; min(₹20, 0.05%) per side intraday
- STT: 0.1% on delivery (both sides), 0.025% intraday (sell side only)
- Exchange txn charges: ~0.00297% (NSE)
- Stamp duty: 0.015% (buy side only)
- GST: 18% on (brokerage + exchange charges)
- Slippage: tier-based, 0.05% large-cap / 0.10% mid-cap / 0.15% small-cap per side

Without this, every simulated P&L number is optimistic fiction — a trade
that "won" +0.8% gross can easily be a net loser after round-trip costs.
"""
from __future__ import annotations

from dataclasses import dataclass

SLIPPAGE_BY_TIER = {"large": 0.0005, "mid": 0.0010, "small": 0.0015}


@dataclass
class TradeCosts:
    brokerage: float
    stt: float
    exchange: float
    stamp: float
    gst: float
    slippage: float
    total: float
    total_pct: float  # as % of trade value


def compute_round_trip_costs(
        entry_price: float, exit_price: float,
        quantity: int = 1, tier: str = "large",
        is_intraday: bool = False) -> TradeCosts:
    """Computes full round-trip (buy + sell) costs for an Indian equity trade."""
    buy_value = entry_price * quantity
    sell_value = exit_price * quantity
    turnover = buy_value + sell_value

    if is_intraday:
        brokerage = min(20, buy_value * 0.0005) + min(20, sell_value * 0.0005)
        stt = sell_value * 0.00025
    else:
        brokerage = 0.0  # Groww: zero delivery brokerage
        stt = turnover * 0.001

    exchange = turnover * 0.0000297
    stamp = buy_value * 0.00015
    gst = (brokerage + exchange) * 0.18
    slippage_rate = SLIPPAGE_BY_TIER.get(tier, 0.0005)
    slippage = turnover * slippage_rate

    total = brokerage + stt + exchange + stamp + gst + slippage
    total_pct = round(total / buy_value * 100, 4) if buy_value else 0

    return TradeCosts(
        brokerage=round(brokerage, 2), stt=round(stt, 2),
        exchange=round(exchange, 2), stamp=round(stamp, 2),
        gst=round(gst, 2), slippage=round(slippage, 2),
        total=round(total, 2), total_pct=total_pct)


def net_pnl_pct(gross_pnl_pct: float, entry: float,
                exit_price: float, tier: str = "large",
                is_intraday: bool = False) -> float:
    """Converts gross P&L% to net P&L% after all costs."""
    costs = compute_round_trip_costs(entry, exit_price, 1, tier, is_intraday)
    return round(gross_pnl_pct - costs.total_pct, 3)
