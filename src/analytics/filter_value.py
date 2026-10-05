"""
Filter value — which entry gates protect money and which only block winners.

EXPLORER enters with no filters, so every closed EXPLORER trade is an unbiased
sample of what the gated engines would have seen with and without each gate.
Each trade stores the pass/block flag of every gate at entry (`filters`);
this module compares win rate and expectancy across that split. Rejected
pipeline candidates (ForwardSimulator) answer the same question for the real
gates: of the candidates blocked for reason X, how many would have won?

Pure code over stored data — no LLM, no broker, no TradeRecord.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

EXPLORER_HISTORY_FILE = Path("data/cache/explorer_history.json")

# Display name per binary gate. True means the gate would let the entry through.
BINARY_FILTERS = {
    "rsi_zone": "RSI zone 48-68",
    "adx_trending": "ADX not choppy",
    "supertrend": "Supertrend bullish",
}
MIN_SAMPLE = 20


def entry_filter_flags(indicators: dict, tier: str, regime: str) -> dict:
    """Pass/block flags (True = gate passes) plus the tier and regime labels,
    stored on the trade at entry so later analysis never recomputes them."""
    ind = indicators or {}
    rsi = ind.get("rsi_14")
    return {
        "rsi_zone": rsi is not None and 48 <= rsi <= 68,
        "adx_trending": ind.get("adx_signal") == "TRENDING",
        "supertrend": ind.get("supertrend_direction") == "BULLISH",
        "tier": tier,
        "regime": regime,
    }


def _score(trades: list[dict]) -> dict:
    from src.analytics.strategy_scorecard import EngineScorecard

    return EngineScorecard._score(trades)


def load_closed_explorer_trades() -> list[dict]:
    if not EXPLORER_HISTORY_FILE.exists():
        return []
    try:
        trades = json.loads(EXPLORER_HISTORY_FILE.read_text())
    except Exception:
        return []
    return [t for t in trades if t.get("outcome") in ("WIN", "LOSS") and t.get("filters")]


def explorer_filter_value(trades: list[dict]) -> dict:
    """{"binary": {gate: {"pass": stats, "block": stats}},
        "tier": {label: stats}, "regime": {label: stats}}"""
    binary = {}
    for gate in BINARY_FILTERS:
        passed = [t for t in trades if t["filters"].get(gate)]
        blocked = [t for t in trades if not t["filters"].get(gate)]
        binary[gate] = {"pass": _score(passed), "block": _score(blocked)}

    def grouped(key: str) -> dict:
        labels = sorted({t["filters"].get(key) or "UNKNOWN" for t in trades})
        return {lab: _score([t for t in trades if (t["filters"].get(key) or "UNKNOWN") == lab])
                for lab in labels}

    return {"binary": binary, "tier": grouped("tier"), "regime": grouped("regime")}


_REASON_TOKEN = re.compile(r"[A-Z][A-Z0-9_]{3,}")


def reason_key(reason: str) -> str:
    """Collapses a free-text rejection reason to a stable group key: the first
    UPPER_SNAKE token if present, else the text before the first digit/colon."""
    text = (reason or "").strip()
    if not text:
        return "UNSPECIFIED"
    match = _REASON_TOKEN.search(text)
    if match:
        return match.group(0)
    return re.split(r"[\d:]", text, maxsplit=1)[0].strip()[:40] or "UNSPECIFIED"


def rejection_reason_value(sims: list) -> dict:
    """Groups filled ForwardSimulator rows by rejection reason:
    {key: {"n", "would_win_rate", "avg_7d_pct"}}. FORCED_* mirror rows are
    engine output, not pipeline rejections, so they never count here."""
    groups: dict[str, list] = {}
    for s in sims:
        if s.would_have_won is None:
            continue
        if (s.rejection_reason or "").startswith("FORCED_LEARNING"):
            continue
        groups.setdefault(reason_key(s.rejection_reason), []).append(s)
    out = {}
    for key, rows in groups.items():
        moves = [s.outcome_7d for s in rows if s.outcome_7d is not None]
        out[key] = {
            "n": len(rows),
            "would_win_rate": round(sum(1 for s in rows if s.would_have_won) / len(rows), 3),
            "avg_7d_pct": round(sum(moves) / len(moves), 2) if moves else None,
        }
    return out


def _fmt(stats: dict) -> str:
    n = stats["n_trades"]
    if n == 0:
        return "n/a"
    mark = "~" if n < MIN_SAMPLE else ""
    return f"{mark}{stats['win_rate'] * 100:.0f}% WR, {stats['expectancy']:+.2f}% exp (n={n})"


def render_filter_value_section() -> str:
    """Telegram-ready 'filter value' block for /learn. Gate keys sit inside
    backticks so underscores survive Markdown parsing."""
    lines = ["🔬 *Filter value* _(EXPLORER = no filters; ~ = n<20)_"]
    trades = load_closed_explorer_trades()
    if trades:
        value = explorer_filter_value(trades)
        for gate, label in BINARY_FILTERS.items():
            pair = value["binary"][gate]
            lines.append(f"• {label}\n    with: {_fmt(pair['pass'])}\n    without: {_fmt(pair['block'])}")
        lines.append("• Tier: " + " · ".join(f"{k} {_fmt(v)}" for k, v in value["tier"].items()))
        lines.append("• Regime:")
        lines += [f"    {k}: {_fmt(v)}" for k, v in value["regime"].items()]
    else:
        lines.append("  EXPLORER has no closed trades yet.")

    try:
        from src.learning.forward_simulator import ForwardSimulator

        reasons = ForwardSimulator().rejection_outcomes()
    except Exception:
        reasons = {}
    lines.append("\n🚧 *Blocked candidates → what happened next (7d)*")
    if reasons:
        for key, r in sorted(reasons.items(), key=lambda kv: -kv[1]["n"])[:8]:
            avg = f", avg {r['avg_7d_pct']:+.1f}%" if r["avg_7d_pct"] is not None else ""
            lines.append(f"  `{key}`: {r['would_win_rate'] * 100:.0f}% would have won{avg} (n={r['n']})")
    else:
        lines.append("  No filled rejection outcomes yet.")
    return "\n".join(lines)
