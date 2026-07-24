"""
Deterministic fundamental ratio extraction. Pure code, no LLM.

Pulls the fields we care about out of a yfinance `.info` dict and normalizes them.
Missing fields become None; floats are rounded to 4 dp. Never divides by zero.
"""
from __future__ import annotations

from src.utils.serialization import sanitize_for_state


def _round(value, ndigits: int = 4):
    try:
        if value is None:
            return None
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return None


def _num(info: dict, key: str):
    try:
        v = info.get(key)
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def piotroski_f_score(info: dict) -> tuple[int, int]:
    """
    Piotroski F-Score (0-9): 9 binary financial-health signals (Piotroski 2000).
    YoY signals require a prior-year value (`<field>_prev`), which a plain yfinance
    `.info` dict never carries — so most installs only ever evaluate F1/F2/F4.
    Returns (score, signals_evaluated) so callers can tell "3/3 evaluable signals
    were bad" apart from "0/9 because 6 signals were structurally unavailable" —
    treating the two the same would hard-reject almost every stock on missing data.
    Never raises.
    """
    info = info or {}
    score = 0
    evaluated = 0

    roa = _num(info, "returnOnAssets")
    ocf = _num(info, "operatingCashflow")
    net_income = _num(info, "netIncome")

    # Profitability (4).
    if roa is not None:
        evaluated += 1
        if roa > 0:                                       # F1 profitable
            score += 1
    if ocf is not None:
        evaluated += 1
        if ocf > 0:                                        # F2 positive cash flow
            score += 1
    roa_prev = _num(info, "returnOnAssets_prev")
    if roa is not None and roa_prev is not None:
        evaluated += 1
        if roa > roa_prev:                                  # F3 ROA up YoY
            score += 1
    if ocf is not None and net_income is not None:
        evaluated += 1
        if ocf > net_income:                                # F4 quality
            score += 1

    # Leverage / liquidity (3).
    ltd, ltd_prev = _num(info, "longTermDebt"), _num(info, "longTermDebt_prev")
    if ltd is not None and ltd_prev is not None:
        evaluated += 1
        if ltd < ltd_prev:                                   # F5 less leverage
            score += 1
    cr, cr_prev = _num(info, "currentRatio"), _num(info, "currentRatio_prev")
    if cr is not None and cr_prev is not None:
        evaluated += 1
        if cr > cr_prev:                                     # F6 better liquidity
            score += 1
    sh, sh_prev = _num(info, "sharesOutstanding"), _num(info, "sharesOutstanding_prev")
    if sh is not None and sh_prev is not None:
        evaluated += 1
        if sh <= sh_prev:                                    # F7 no dilution
            score += 1

    # Operating efficiency (2).
    gm, gm_prev = _num(info, "grossMargins"), _num(info, "grossMargins_prev")
    if gm is not None and gm_prev is not None:
        evaluated += 1
        if gm > gm_prev:                                     # F8 margin up
            score += 1
    rev, assets = _num(info, "totalRevenue"), _num(info, "totalAssets")
    rev_p, assets_p = _num(info, "totalRevenue_prev"), _num(info, "totalAssets_prev")
    if all(v is not None for v in (rev, assets, rev_p, assets_p)) and assets and assets_p:
        evaluated += 1
        if (rev / assets) > (rev_p / assets_p):              # F9 asset turnover up
            score += 1

    return score, evaluated


def _piotroski_label(score: int) -> str:
    if score >= 7:
        return "STRONG"
    if score >= 4:
        return "NEUTRAL"
    return "WEAK"


def compute_ratios(info: dict) -> dict:
    info = info or {}

    # FCF yield = free cash flow / market cap (Buffett's preferred valuation read).
    fcf = _num(info, "freeCashflow")
    mcap = _num(info, "marketCap")
    fcf_yield = round(fcf / mcap, 4) if (fcf is not None and mcap) else None
    if fcf_yield is None:
        fcf_label = "UNKNOWN"
    elif fcf_yield > 0.08:
        fcf_label = "VERY_ATTRACTIVE"
    elif fcf_yield > 0.05:
        fcf_label = "ATTRACTIVE"
    elif fcf_yield > 0.02:
        fcf_label = "FAIR"
    else:
        fcf_label = "EXPENSIVE"

    pscore, pscore_evaluated = piotroski_f_score(info)

    # debt_to_equity comes from yfinance as a percentage (e.g. 45.2) — keep as-is, rounded.
    return sanitize_for_state({
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
        # ── Phase B additions ──
        "piotroski_score": pscore,
        "piotroski_signals_evaluated": pscore_evaluated,
        "piotroski_label": _piotroski_label(pscore) if pscore_evaluated >= 5 else "INSUFFICIENT_DATA",
        "fcf_yield": fcf_yield,
        "fcf_yield_label": fcf_label,
        "ev_ebitda": _round(info.get("enterpriseToEbitda")),
        "payout_ratio": _round(info.get("payoutRatio")),
    })
