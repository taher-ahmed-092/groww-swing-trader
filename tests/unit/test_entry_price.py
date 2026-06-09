"""Entry-zone recommendation behavior."""
from __future__ import annotations

from src.agents.technical.entry_price import recommend_entry


def test_extended_price_suggests_patience():
    ind = {"ma_50": 100.0, "atr_14": 2.0}
    out = recommend_entry(ind, current_price=105.0)  # 2.5×ATR above MA50
    assert out["patience_needed"] is True


def test_near_ma50_suggests_entry():
    ind = {"ma_50": 100.0, "atr_14": 2.0}
    out = recommend_entry(ind, current_price=100.5)  # 0.25×ATR above MA50
    assert out["patience_needed"] is False


def test_entry_zone_contains_current_price():
    ind = {"ma_50": 100.0, "atr_14": 2.0}
    current = 100.5
    out = recommend_entry(ind, current_price=current)
    assert out["entry_zone_low"] <= current <= out["entry_zone_high"]
