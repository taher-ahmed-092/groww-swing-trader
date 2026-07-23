"""
Shared merged-trade-history loader — real + forced + intraday + short, each
tagged with its source. Used by both the dashboard (P&L chart, scoreboard) and
PerformanceAnalyzer.get_professional_metrics() so the two never drift out of
sync on what counts as "all trades."
"""
from __future__ import annotations

import json
from pathlib import Path

from src.memory.journal import TradingJournal


def load_all_trade_history(journal: TradingJournal | None = None) -> list[dict]:
    journal = journal or TradingJournal()
    merged: list[dict] = []
    try:
        for t in journal.get_recent(n=50):
            if t.outcome in ("WIN", "LOSS") and t.closed_at:
                merged.append({
                    "symbol": t.symbol, "pnl": t.pnl_pct or 0,
                    "gross_pnl": t.gross_pnl_pct if t.gross_pnl_pct is not None else (t.pnl_pct or 0),
                    "outcome": t.outcome, "source": "real", "closed_at": t.closed_at.isoformat(),
                })
    except Exception:
        pass

    for fname, source in (
        ("data/cache/forced_trades_history.json", "forced"),
        ("data/cache/intraday_sim_history.json", "intraday"),
        ("data/cache/short_trades_history.json", "short"),
    ):
        p = Path(fname)
        if p.exists():
            try:
                for t in json.loads(p.read_text()):
                    if t.get("outcome") in ("WIN", "LOSS"):
                        merged.append({
                            "symbol": t.get("symbol", "?"), "pnl": t.get("pnl_pct", 0) or 0,
                            "gross_pnl": t.get("gross_pnl_pct", t.get("pnl_pct", 0)) or 0,
                            "outcome": t.get("outcome"), "source": source,
                            "closed_at": t.get("closed_at", t.get("opened_at", "")),
                        })
            except Exception:
                pass

    merged.sort(key=lambda x: x.get("closed_at", "") or "")
    return merged
