"""
Deterministic technical indicators. Pure code over OHLCV, no LLM.

Uses the `ta` library plus hand-rolled candlestick / Fibonacci math. Input df must
have Open/High/Low/Close/Volume columns. Never raises: any indicator that can't be
computed comes back as None (or "NONE"/"NEUTRAL" for categorical fields).
"""
from __future__ import annotations

import pandas as pd
from ta.momentum import RSIIndicator, StochRSIIndicator
from ta.trend import MACD, ADXIndicator, SMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands
from ta.volume import OnBalanceVolumeIndicator


def _last(series) -> float | None:
    try:
        value = series.iloc[-1]
        if pd.isna(value):
            return None
        return round(float(value), 4)
    except Exception:
        return None


def _detect_candlestick(df: pd.DataFrame) -> str:
    """Detect a pattern in the last 1-3 candles from raw OHLC math. 'NONE' if none."""
    try:
        if len(df) < 2:
            return "NONE"
        o = df["Open"].astype(float).values
        h = df["High"].astype(float).values
        low_ = df["Low"].astype(float).values
        c = df["Close"].astype(float).values

        co, ch, cl, cc = o[-1], h[-1], low_[-1], c[-1]
        po, pc = o[-2], c[-2]

        rng = ch - cl
        if rng <= 0:
            return "NONE"
        body = abs(cc - co)
        upper = ch - max(co, cc)
        lower = min(co, cc) - cl

        # Three-candle star patterns first (highest information content).
        if len(df) >= 3:
            fo, fc = o[-3], c[-3]
            f_body = abs(fc - fo)
            mid_first = (fo + fc) / 2
            star_body = abs(pc - po)
            # small middle candle relative to first
            small_star = f_body > 0 and star_body <= 0.5 * f_body
            if fc < fo and small_star and cc > co and cc > mid_first:
                return "MORNING_STAR"
            if fc > fo and small_star and cc < co and cc < mid_first:
                return "EVENING_STAR"

        # Engulfing (two candles).
        if pc < po and cc > co and co <= pc and cc >= po:
            return "BULLISH_ENGULFING"
        if pc > po and cc < co and co >= pc and cc <= po:
            return "BEARISH_ENGULFING"

        # Single-candle shapes.
        if body <= 0.1 * rng:
            return "DOJI"
        if lower >= 2 * body and upper <= body and body > 0:
            return "HAMMER"
        if upper >= 2 * body and lower <= body and body > 0:
            return "INVERTED_HAMMER"

        return "NONE"
    except Exception:
        return "NONE"


def compute_indicators(df: pd.DataFrame) -> dict:
    result: dict = {
        "rsi_14": None, "macd": None, "macd_signal": None, "macd_hist": None,
        "bb_upper": None, "bb_mid": None, "bb_lower": None,
        "ma_50": None, "ma_200": None, "atr_14": None, "volume_ratio": None,
        "trend": "SIDEWAYS", "support": None, "resistance": None, "rsi_signal": "NEUTRAL",
        # ── upgrade additions ──
        "adx_14": None, "adx_signal": "NEUTRAL", "obv": None, "obv_trend": "FALLING",
        "stoch_rsi": None, "candlestick_pattern": "NONE", "fib_levels": {}, "atr_stop": None,
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
            result["atr_14"] = _last(
                AverageTrueRange(high, low, close, window=14).average_true_range()
            )
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

    # ── ADX (trend strength) ──
    try:
        if n >= 28:
            adx = _last(ADXIndicator(high, low, close, window=14).adx())
            result["adx_14"] = adx
            if adx is not None:
                if adx > 25:
                    result["adx_signal"] = "TRENDING"
                elif adx < 20:
                    result["adx_signal"] = "CHOPPY"
                else:
                    result["adx_signal"] = "NEUTRAL"
    except Exception:
        pass

    # ── OBV (volume conviction) ──
    try:
        if n >= 6:
            obv_series = OnBalanceVolumeIndicator(close, volume).on_balance_volume()
            result["obv"] = _last(obv_series)
            tail = obv_series.dropna().tail(5)
            if len(tail) >= 2:
                slope = float(tail.iloc[-1] - tail.iloc[0])
                result["obv_trend"] = "RISING" if slope > 0 else "FALLING"
    except Exception:
        pass

    # ── Stochastic RSI (0-100) ──
    try:
        if n >= 20:
            srsi = StochRSIIndicator(close, window=14).stochrsi()
            val = _last(srsi)
            result["stoch_rsi"] = round(val * 100, 4) if val is not None else None
    except Exception:
        pass

    # ── Candlestick pattern ──
    result["candlestick_pattern"] = _detect_candlestick(df)

    # ── Fibonacci retracement (from 20-day high/low) ──
    try:
        if n >= 20:
            hi = float(high.rolling(window=20).max().iloc[-1])
            lo = float(low.rolling(window=20).min().iloc[-1])
            diff = hi - lo
            result["fib_levels"] = {
                str(r): round(hi - diff * r, 4)
                for r in (0.236, 0.382, 0.5, 0.618, 0.786)
            }
    except Exception:
        pass

    # ── ATR-based volatility stop (entry - 2*ATR) ──
    try:
        price = float(close.iloc[-1])
        if result["atr_14"] is not None:
            result["atr_stop"] = round(price - 2 * result["atr_14"], 4)
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
