"""
Deterministic fundamental ratio extraction. Pure code, no LLM.

Pulls the fields we care about out of a yfinance `.info` dict and normalizes them.
Missing fields become None; floats are rounded to 4 dp. Never divides by zero.
"""
from __future__ import annotations


def _round(value, ndigits: int = 4):
    try:
        if value is None:
            return None
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return None


def compute_ratios(info: dict) -> dict:
    info = info or {}

    # debt_to_equity comes from yfinance as a percentage (e.g. 45.2) — keep as-is, rounded.
    return {
        "pe_ratio": _round(info.get("trailingPE")),
        "pb_ratio": _round(info.get("priceToBook")),
        "ps_ratio": _round(info.get("priceToSalesTrailing12Months")),
        "roe": _round(info.get("returnOnEquity")),
        "roa": _round(info.get("returnOnAssets")),
        "debt_to_equity": _round(info.get("debtToEquity")),
        "revenue_growth": _round(info.get("revenueGrowth")),
        "earnings_growth": _round(info.get("earningsGrowth")),
        "free_cashflow": _round(info.get("freeCashflow")),
        "market_cap": _round(info.get("marketCap")),
        "dividend_yield": _round(info.get("dividendYield")),
        "current_ratio": _round(info.get("currentRatio")),
        "quick_ratio": _round(info.get("quickRatio")),
    }
