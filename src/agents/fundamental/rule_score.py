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
    """Score 0-10 (returned as 0-1) from ROCE, D/E, growth, promoter, P/E."""
    data = {**(ratios or {}), **(screener_data or {})}
    points = 0.0
    strengths: list[str] = []
    weaknesses: list[str] = []

    # ROCE (0-3).
    roce = _f(data.get("roce_pct"))
    if roce is not None:
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
    if pe is not None and 0 < pe <= 35:
        points += 1
    elif pe is not None and pe > 60:
        weaknesses.append(f"Expensive P/E {pe}")

    # Piotroski F-Score (research-backed financial health).
    pscore = data.get("piotroski_score")
    if pscore is not None:
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
        if fcf_yield > 0.08:
            points += 1.5
            strengths.append(f"High FCF yield ({fcf_yield:.1%})")
        elif fcf_yield > 0.05:
            points += 1
        elif fcf_yield < 0.02:
            weaknesses.append(f"Low FCF yield ({fcf_yield:.1%})")

    # Normalize to 0-1 (clamp — extra signals can push points past 10).
    score = round(max(0.0, min(1.0, points / 10.0)), 4)

    if score >= 0.7:
        moat_strength = "STRONG"
    elif score >= 0.5:
        moat_strength = "MODERATE"
    elif score >= 0.3:
        moat_strength = "WEAK"
    else:
        moat_strength = "NONE"

    return {
        "score": score,
        "proceed": score >= 0.55,
        "hard_rejected": False,
        "reasoning": f"Rule-based fundamental score {points:.1f}/10 (demo mode).",
        "strengths": strengths,
        "weaknesses": weaknesses,
        "moat_strength": moat_strength,
        "swot": {"strengths": strengths, "weaknesses": weaknesses,
                 "opportunities": [], "threats": []},
        "demo_mode": True,
    }
