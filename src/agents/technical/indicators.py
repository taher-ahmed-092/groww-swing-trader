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
from ta.volume import (
    ChaikinMoneyFlowIndicator,
    OnBalanceVolumeIndicator,
    VolumeWeightedAveragePrice,
)

from src.utils.serialization import sanitize_for_state


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

        # Three-candle patterns first (highest information content).
        if len(df) >= 3:
            fo, fc = o[-3], c[-3]
            f_body = abs(fc - fo)
            mid_first = (fo + fc) / 2
            star_body = abs(pc - po)
            star_rng = (h[-2] - low_[-2]) or 1e-9
            small_star = f_body > 0 and star_body <= 0.5 * f_body
            star_is_doji = star_body <= 0.1 * star_rng

            # Three white soldiers / black crows — 3 consecutive directional candles.
            if c[-3] > o[-3] and pc > po and cc > co and cc > pc > c[-3]:
                return "THREE_WHITE_SOLDIERS"
            if c[-3] < o[-3] and pc < po and cc < co and cc < pc < c[-3]:
                return "THREE_BLACK_CROWS"

            if fc < fo and small_star and cc > co and cc > mid_first:
                return "MORNING_DOJI_STAR" if star_is_doji else "MORNING_STAR"
            if fc > fo and small_star and cc < co and cc < mid_first:
                return "EVENING_STAR"

        # Two-candle patterns.
        if pc < po and cc > co and co <= pc and cc >= po:
            return "BULLISH_ENGULFING"
        if pc > po and cc < co and co >= pc and cc <= po:
            return "BEARISH_ENGULFING"
        prev_mid = (po + pc) / 2
        # Dark cloud cover: prior bullish, current opens above prior close, closes below its midpoint.
        if pc > po and co > pc and cc < prev_mid and cc > po:
            return "DARK_CLOUD_COVER"
        # Piercing line: prior bearish, current opens below prior close, closes above its midpoint.
        if pc < po and co < pc and cc > prev_mid and cc < po:
            return "PIERCING_LINE"

        # Single-candle shapes.
        if body <= 0.1 * rng and upper >= 0.3 * rng and lower >= 0.3 * rng:
            return "SPINNING_TOP"
        if body <= 0.1 * rng:
            return "DOJI"
        if lower >= 2 * body and upper <= body and body > 0:
            return "HAMMER"
        if upper >= 2 * body and lower <= body and body > 0:
            return "INVERTED_HAMMER"

        return "NONE"
    except Exception:
        return "NONE"


_HIGH_CONFIDENCE_PATTERNS = {
    "BULLISH_ENGULFING", "MORNING_STAR", "THREE_WHITE_SOLDIERS", "PIERCING_LINE", "HAMMER",
}
_MEDIUM_CONFIDENCE_PATTERNS = {"MORNING_DOJI_STAR", "INVERTED_HAMMER"}
_LOW_CONFIDENCE_PATTERNS = {"DOJI", "SPINNING_TOP"}


def _candlestick_confidence(pattern: str) -> str:
    if pattern in _HIGH_CONFIDENCE_PATTERNS:
        return "HIGH"
    if pattern in _MEDIUM_CONFIDENCE_PATTERNS:
        return "MEDIUM"
    if pattern in _LOW_CONFIDENCE_PATTERNS:
        return "LOW"
    return "NONE"


def _supertrend(df: pd.DataFrame, period: int = 14, multiplier: float = 2.0):
    """Return (supertrend_value, direction) over the last candles. None on failure."""
    try:
        if len(df) < period + 2:
            return None, None
        high, low, close = df["High"], df["Low"], df["Close"]
        atr = AverageTrueRange(high, low, close, window=period).average_true_range()
        hl2 = (high + low) / 2
        upper = (hl2 + multiplier * atr).values
        lower = (hl2 - multiplier * atr).values
        c = close.values
        st = [0.0] * len(c)
        direction = [True] * len(c)  # True = bullish (price above supertrend)
        start = period
        st[start] = lower[start]
        for i in range(start + 1, len(c)):
            if c[i] > st[i - 1]:
                direction[i] = True
            elif c[i] < st[i - 1]:
                direction[i] = False
            else:
                direction[i] = direction[i - 1]
            st[i] = lower[i] if direction[i] else upper[i]
        if pd.isna(st[-1]):
            return None, None
        return round(float(st[-1]), 4), ("BULLISH" if direction[-1] else "BEARISH")
    except Exception:
        return None, None


def compute_indicators(df: pd.DataFrame) -> dict:
    result: dict = {
        "rsi_14": None, "macd": None, "macd_signal": None, "macd_hist": None,
        "bb_upper": None, "bb_mid": None, "bb_lower": None,
        "ma_50": None, "ma_200": None, "atr_14": None, "volume_ratio": None,
        "trend": "SIDEWAYS", "support": None, "resistance": None, "rsi_signal": "NEUTRAL",
        # ── upgrade additions ──
        "adx_14": None, "adx_signal": "NEUTRAL", "obv": None, "obv_trend": "FALLING",
        "stoch_rsi": None, "candlestick_pattern": "NONE", "candlestick_confidence": "NONE",
        "fib_levels": {}, "atr_stop": None,
        # ── Phase 4 additions ──
        "ichimoku_conversion": None, "ichimoku_base": None, "ichimoku_signal": "NEUTRAL",
        "ichimoku_cloud_color": None, "vwap": None, "price_vs_vwap": None,
        "pivot": None, "r1": None, "r2": None, "s1": None, "s2": None,
        "nearest_pivot_level": None, "cmf_20": None, "cmf_signal": "NEUTRAL",
        "supertrend": None, "supertrend_direction": None,
        "pct_from_52w_high": None, "pct_from_52w_low": None, "week52_position": None,
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

    # ── Candlestick pattern + confidence ──
    result["candlestick_pattern"] = _detect_candlestick(df)
    result["candlestick_confidence"] = _candlestick_confidence(result["candlestick_pattern"])

    price = float(close.iloc[-1])

    # ── Ichimoku (manual) ──
    try:
        if n >= 52:
            conv = (high.rolling(9).max().iloc[-1] + low.rolling(9).min().iloc[-1]) / 2
            base = (high.rolling(26).max().iloc[-1] + low.rolling(26).min().iloc[-1]) / 2
            span_a = (conv + base) / 2
            span_b = (high.rolling(52).max().iloc[-1] + low.rolling(52).min().iloc[-1]) / 2
            result["ichimoku_conversion"] = round(float(conv), 4)
            result["ichimoku_base"] = round(float(base), 4)
            result["ichimoku_cloud_color"] = "GREEN" if span_a > span_b else "RED"
            if price > conv and price > base:
                result["ichimoku_signal"] = "BULLISH"
            elif price < conv and price < base:
                result["ichimoku_signal"] = "BEARISH"
            else:
                result["ichimoku_signal"] = "NEUTRAL"
    except Exception:
        pass

    # ── VWAP ──
    try:
        if n >= 20:
            vwap = VolumeWeightedAveragePrice(
                high=high, low=low, close=close, volume=volume, window=20
            ).volume_weighted_average_price()
            result["vwap"] = _last(vwap)
            if result["vwap"] is not None:
                result["price_vs_vwap"] = "ABOVE" if price > result["vwap"] else "BELOW"
    except Exception:
        pass

    # ── Pivot points (from the previous completed session) ──
    try:
        if n >= 2:
            ph, pl, pc_ = float(high.iloc[-2]), float(low.iloc[-2]), float(close.iloc[-2])
            pivot = (ph + pl + pc_) / 3
            result["pivot"] = round(pivot, 4)
            result["r1"] = round(2 * pivot - pl, 4)
            result["r2"] = round(pivot + (ph - pl), 4)
            result["s1"] = round(2 * pivot - ph, 4)
            result["s2"] = round(pivot - (ph - pl), 4)
            if price >= result["r1"]:
                result["nearest_pivot_level"] = "ABOVE_R1"
            elif price >= pivot:
                result["nearest_pivot_level"] = "NEAR_PIVOT"
            elif price >= result["s1"]:
                result["nearest_pivot_level"] = "ABOVE_S1"
            else:
                result["nearest_pivot_level"] = "BELOW_S1"
    except Exception:
        pass

    # ── CMF (Chaikin Money Flow) ──
    try:
        if n >= 20:
            cmf = ChaikinMoneyFlowIndicator(high, low, close, volume, window=20).chaikin_money_flow()
            result["cmf_20"] = _last(cmf)
            if result["cmf_20"] is not None:
                if result["cmf_20"] > 0.1:
                    result["cmf_signal"] = "BUYING_PRESSURE"
                elif result["cmf_20"] < -0.1:
                    result["cmf_signal"] = "SELLING_PRESSURE"
                else:
                    result["cmf_signal"] = "NEUTRAL"
    except Exception:
        pass

    # ── Supertrend ──
    st_val, st_dir = _supertrend(df)
    result["supertrend"] = st_val
    result["supertrend_direction"] = st_dir

    # ── 52-week position ──
    try:
        hi = float(high.max())
        lo = float(low.min())
        if hi > lo:
            result["pct_from_52w_high"] = round((price - hi) / hi * 100, 2)
            result["pct_from_52w_low"] = round((price - lo) / lo * 100, 2)
            pos = (price - lo) / (hi - lo)
            if pos > 0.9:
                result["week52_position"] = "NEAR_HIGH"
            elif pos >= 0.4:
                result["week52_position"] = "MID"
            else:
                result["week52_position"] = "NEAR_LOW"
    except Exception:
        pass

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

    return sanitize_for_state(result)
