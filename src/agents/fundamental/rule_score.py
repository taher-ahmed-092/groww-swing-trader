"""
Rule-based fundamental scoring — the deterministic engine behind DEMO mode.

No LLM, no network beyond the data already gathered. Produces a 0-1 quality score
from the same numbers the LLM would interpret, so paper-mode infrastructure works
end-to-end with zero credentials (CLAUDE.md rule 4: code computes, never invents).
"""
from __future__ import annotations


def _f(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def rule_based_fundamental_score(ratios: dict, screener_data: dict) -> dict:
    """Score 0-10 (returned as 0-1) from ROCE, D/E, growth, promoter, P/E.

    When NO dimension has usable data (the common case in demo mode without a
    screener feed), return a neutral 0.50 with data_missing=True rather than 0.00 —
    absent data is "unknown", not "bad", and a hard 0.00 would reject every paper
    trade. Genuinely-weak-but-present data still scores low (CLAUDE.md rule 4: we
    never invent numbers to flatter a stock).
    """
    data = {**(ratios or {}), **(screener_data or {})}
    points = 0.0
    dims_with_data = 0
    strengths: list[str] = []
    weaknesses: list[str] = []

    # ROCE (0-3).
    roce = _f(data.get("roce_pct"))
    if roce is not None:
        dims_with_data += 1
        if roce >= 20:
            points += 3
            strengths.append(f"Excellent ROCE {roce}%")
        elif roce >= 15:
            points += 2
            strengths.append(f"Strong ROCE {roce}%")
        elif roce >= 10:
            points += 1
        else:
            weaknesses.append(f"Weak ROCE {roce}%")

    # Debt/Equity (0-2).
    dte = _f(data.get("debt_to_equity"))
    if dte is not None:
        dims_with_data += 1
        if dte < 0.5:
            points += 2
            strengths.append(f"Low debt (D/E {dte})")
        elif dte < 1.0:
            points += 1
        elif dte > 2.0:
            weaknesses.append(f"High debt (D/E {dte})")

    # 3yr growth (0-2).
    sales = _f(data.get("sales_growth_3yr"))
    profit = _f(data.get("profit_growth_3yr"))
    if sales is not None and profit is not None:
        dims_with_data += 1
        if sales > 10 and profit > 10:
            points += 2
            strengths.append("Improving 3yr sales & profit growth")
        elif sales > 0 and profit > 0:
            points += 1
        elif sales < 0 or profit < 0:
            weaknesses.append("Declining 3yr growth")

    # Promoter holding + pledging (0-2).
    holding = _f(data.get("promoter_holding_pct"))
    pledged = _f(data.get("promoter_pledged_pct"))
    if holding is not None or pledged is not None:
        dims_with_data += 1
    promoter_pts = 0
    if holding is not None and holding >= 50:
        promoter_pts += 1
        strengths.append(f"High promoter holding {holding}%")
    if pledged is not None and pledged < 5:
        promoter_pts += 1
    elif pledged is not None and pledged > 30:
        weaknesses.append(f"High promoter pledging {pledged}%")
    points += promoter_pts

    # P/E reasonableness (0-1).
    pe = _f(data.get("pe_ratio"))
    if pe is not None:
        dims_with_data += 1
        if 0 < pe <= 35:
            points += 1
        elif pe > 60:
            weaknesses.append(f"Expensive P/E {pe}")

    # Piotroski F-Score (research-backed financial health).
    pscore = data.get("piotroski_score")
    if pscore is not None:
        dims_with_data += 1
        if pscore >= 7:
            points += 2
            strengths.append(f"Strong Piotroski (F={pscore})")
        elif pscore >= 4:
            points += 1
        elif pscore <= 3:
            points -= 1
            weaknesses.append(f"Weak Piotroski (F={pscore})")

    # FCF yield (Buffett's valuation read).
    fcf_yield = _f(data.get("fcf_yield"))
    if fcf_yield is not None:
        dims_with_data += 1
        if fcf_yield > 0.08:
            points += 1.5
            strengths.append(f"High FCF yield ({fcf_yield:.1%})")
        elif fcf_yield > 0.05:
            points += 1
        elif fcf_yield < 0.02:
            weaknesses.append(f"Low FCF yield ({fcf_yield:.1%})")

    # Normalize to 0-1 (clamp — extra signals can push points past 10).
    score = round(max(0.0, min(1.0, points / 10.0)), 4)

    # No usable data at all → neutral/unknown, not a hard 0.00 rejection.
    data_missing = dims_with_data == 0
    if data_missing:
        score = 0.50
        weaknesses.append("No fundamental data available — scored neutral")

    if score >= 0.7:
        moat_strength = "STRONG"
    elif score >= 0.5:
        moat_strength = "MODERATE"
    elif score >= 0.3:
        moat_strength = "WEAK"
    else:
        moat_strength = "NONE"

    reasoning = (
        "No fundamental data — neutral 0.50 (demo mode)."
        if data_missing
        else f"Rule-based fundamental score {points:.1f}/10 (demo mode)."
    )

    return {
        "score": score,
        # Neutral/unknown should proceed to the judge rather than be rejected here.
        "proceed": score >= 0.55 or data_missing,
        "hard_rejected": False,
        "reasoning": reasoning,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "moat_strength": moat_strength,
        "swot": {"strengths": strengths, "weaknesses": weaknesses,
                 "opportunities": [], "threats": []},
        "demo_mode": True,
        "data_missing": data_missing,
    }
