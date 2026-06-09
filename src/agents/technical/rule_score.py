"""
Rule-based technical scoring — the deterministic engine behind DEMO mode.

Scores a setup 0-10 from indicator values already computed in indicators.py and
emits a BUY/HOLD/SKIP signal. No LLM, no network (CLAUDE.md rule 4).
"""
from __future__ import annotations


def rule_based_technical_score(
    indicators: dict, entry: float, stop: float, target: float
) -> dict:
    indicators = indicators or {}
    points = 0.0
    patterns: list[str] = []

    # RSI zone (0-3).
    rsi = indicators.get("rsi_14")
    if rsi is not None:
        if 50 <= rsi <= 65:
            points += 3
        elif 45 <= rsi < 50:
            points += 2
        elif 40 <= rsi < 45:
            points += 1

    # Trend (0-3).
    trend = indicators.get("trend")
    if trend == "UPTREND":
        points += 3
    elif trend == "SIDEWAYS":
        points += 1

    # ADX (0-2).
    adx_signal = indicators.get("adx_signal")
    if adx_signal == "TRENDING":
        points += 2
    elif adx_signal == "NEUTRAL":
        points += 1

    # Volume (0-1).
    vr = indicators.get("volume_ratio")
    if vr is not None and vr >= 1.2:
        points += 1

    # OBV (0-1).
    if indicators.get("obv_trend") == "RISING":
        points += 1

    candlestick = indicators.get("candlestick_pattern")
    if candlestick and candlestick != "NONE":
        patterns.append(candlestick)

    score = round(points / 10.0, 4)
    weekly_trend = indicators.get("weekly_trend", "SIDEWAYS")

    # BUY only with conviction, a live (non-down) trend, and a non-choppy tape.
    is_buy = score >= 0.65 and trend != "DOWNTREND" and adx_signal != "CHOPPY"
    signal = "BUY" if is_buy else ("HOLD" if score >= 0.4 else "SKIP")

    return {
        "score": score,
        "signal": signal,
        "proceed": signal == "BUY",
        "entry_price": entry,
        "stop_price": stop,
        "target_price": target,
        "reasoning": f"Rule-based technical score {points:.1f}/10, trend={trend} (demo mode).",
        "patterns": patterns,
        "indicators": indicators,
        "weekly_trend": weekly_trend,
        "demo_mode": True,
    }
