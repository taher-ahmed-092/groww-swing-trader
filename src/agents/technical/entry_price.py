"""
Entry-zone recommendation.

Recommends an optimal entry ZONE rather than "buy at any price". A better entry =
a better R:R = more profitable over time. Pure code over computed indicators.
"""
from __future__ import annotations


def recommend_entry(indicators: dict, current_price: float) -> dict:
    indicators = indicators or {}
    atr = indicators.get("atr_14") or (current_price * 0.02)
    ma50 = indicators.get("ma_50") or current_price
    # fib_levels is keyed by string ratio (see indicators.py).
    fib_levels = indicators.get("fib_levels") or {}
    fib_levels.get("0.382", current_price * 0.98)

    distance_from_ma50 = (current_price - ma50) / atr if atr > 0 else 0

    if distance_from_ma50 > 1.5:
        recommended = ma50 + 0.5 * atr
        patience = True
        rationale = (
            f"Price extended {distance_from_ma50:.1f}×ATR above MA50. "
            f"Wait for pullback to ₹{recommended:.2f}"
        )
    elif distance_from_ma50 > 0:
        recommended = current_price
        patience = False
        rationale = f"Price near MA50 support at ₹{ma50:.2f}. Good entry zone."
    else:
        recommended = ma50
        patience = True
        rationale = f"Price below MA50 (₹{ma50:.2f}). Wait for reclaim before entering."

    return {
        "recommended_entry": round(recommended, 2),
        "entry_zone_low": round(recommended - 0.3 * atr, 2),
        "entry_zone_high": round(recommended + 0.3 * atr, 2),
        "entry_rationale": rationale,
        "patience_needed": patience,
        "max_wait_days": 5 if patience else 0,
        "current_vs_recommended_pct": round(
            (current_price - recommended) / recommended * 100, 2
        ) if recommended else 0.0,
    }
