"""
Deterministic technical indicators. Pure code over OHLCV, no LLM.

Uses the `ta` library. Input df must have Open/High/Low/Close/Volume columns.
Never raises: any indicator that can't be computed comes back as None.
"""
from __future__ import annotations

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD, SMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands


def _last(series) -> float | None:
    try:
        value = series.iloc[-1]
        if pd.isna(value):
            return None
        return round(float(value), 4)
    except Exception:
        return None


def compute_indicators(df: pd.DataFrame) -> dict:
    result: dict = {
        "rsi_14": None, "macd": None, "macd_signal": None, "macd_hist": None,
        "bb_upper": None, "bb_mid": None, "bb_lower": None,
        "ma_50": None, "ma_200": None, "atr_14": None, "volume_ratio": None,
        "trend": "SIDEWAYS", "support": None, "resistance": None, "rsi_signal": "NEUTRAL",
    }

    if df is None or df.empty:
        return result

    close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]
    n = len(df)

    try:
        if n >= 14:
            result["rsi_14"] = _last(RSIIndicator(close, window=14).rsi())
    except Exception:
        pass

    try:
        if n >= 26:
            macd = MACD(close)
            result["macd"] = _last(macd.macd())
            result["macd_signal"] = _last(macd.macd_signal())
            result["macd_hist"] = _last(macd.macd_diff())
    except Exception:
        pass

    try:
        if n >= 20:
            bb = BollingerBands(close, window=20)
            result["bb_upper"] = _last(bb.bollinger_hband())
            result["bb_mid"] = _last(bb.bollinger_mavg())
            result["bb_lower"] = _last(bb.bollinger_lband())
    except Exception:
        pass

    try:
        if n >= 50:
            result["ma_50"] = _last(SMAIndicator(close, window=50).sma_indicator())
        if n >= 200:
            result["ma_200"] = _last(SMAIndicator(close, window=200).sma_indicator())
    except Exception:
        pass

    try:
        if n >= 14:
            result["atr_14"] = _last(AverageTrueRange(high, low, close, window=14).average_true_range())
    except Exception:
        pass

    try:
        if n >= 20:
            avg_vol = volume.rolling(window=20).mean().iloc[-1]
            latest_vol = volume.iloc[-1]
            if avg_vol and avg_vol > 0:
                result["volume_ratio"] = round(float(latest_vol / avg_vol), 4)
    except Exception:
        pass

    try:
        if n >= 20:
            result["support"] = round(float(low.rolling(window=20).min().iloc[-1]), 4)
            result["resistance"] = round(float(high.rolling(window=20).max().iloc[-1]), 4)
    except Exception:
        pass

    # Trend: price vs MA50 / MA200.
    try:
        price = float(close.iloc[-1])
        ma50, ma200 = result["ma_50"], result["ma_200"]
        if ma50 is not None and ma200 is not None:
            if price > ma50 and price > ma200 and ma50 > ma200:
                result["trend"] = "UPTREND"
            elif price < ma50 and price < ma200 and ma50 < ma200:
                result["trend"] = "DOWNTREND"
            else:
                result["trend"] = "SIDEWAYS"
        elif ma50 is not None:
            result["trend"] = "UPTREND" if price > ma50 else "DOWNTREND"
    except Exception:
        pass

    # RSI signal bucket.
    rsi = result["rsi_14"]
    if rsi is not None:
        if rsi < 30:
            result["rsi_signal"] = "OVERSOLD"
        elif rsi > 70:
            result["rsi_signal"] = "OVERBOUGHT"
        else:
            result["rsi_signal"] = "NEUTRAL"

    return result
