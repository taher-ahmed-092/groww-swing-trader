"""Circuit-breaker volatility risk assessment."""
from __future__ import annotations

import pandas as pd

from src.risk.circuit_breaker_check import CircuitBreakerRisk


def _df(closes):
    return pd.DataFrame({"Open": closes, "High": [c + 1 for c in closes],
                         "Low": [c - 1 for c in closes], "Close": closes,
                         "Volume": [100000] * len(closes)})


def test_high_frequency_flagged():
    # ~30 rows (~1.4 months) with 4 violent (>4.5%) moves → >2/month → HIGH.
    closes = [100.0] * 30
    for i in (5, 10, 15, 20):
        closes[i] = closes[i - 1] * 1.08  # +8% jump
    out = CircuitBreakerRisk().assess_risk("X", _df(closes))
    assert out["risk_level"] == "HIGH"
    assert out["size_factor"] == 0.5


def test_low_frequency_ok():
    closes = [100.0 + i * 0.05 for i in range(60)]  # calm, no big moves
    out = CircuitBreakerRisk().assess_risk("X", _df(closes))
    assert out["risk_level"] == "LOW"
    assert out["size_factor"] == 1.0


def test_insufficient_data_low():
    out = CircuitBreakerRisk().assess_risk("X", _df([100.0] * 5))
    assert out["risk_level"] == "LOW"
