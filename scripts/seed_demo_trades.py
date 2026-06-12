"""
Seed 6 realistic closed demo trades (3 wins, 3 losses) so the learning loop and
agent-evaluation framework have data to work with. Runs RCA on losses and
win-confirmation on wins, then prints the resulting knowledge base.

    uv run python scripts/seed_demo_trades.py
"""
from __future__ import annotations

import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from rich.console import Console  # noqa: E402
from sqlmodel import Session  # noqa: E402

from src.memory.journal import TradingJournal  # noqa: E402
from src.memory.rca import RootCauseAnalyzer  # noqa: E402

console = Console()

# (symbol, sector, fund_score, tech_score, judge_score, signal, nifty_trend, outcome, pnl_pct)
DEMO_TRADES = [
    ("HDFCBANK", "Banking", 0.82, 0.80, 8.4, "BUY", "UPTREND", "WIN", 9.2),
    ("INFY", "IT", 0.78, 0.76, 7.8, "BUY", "UPTREND", "WIN", 6.5),
    ("RELIANCE", "Energy", 0.85, 0.72, 7.6, "BUY", "SIDEWAYS", "WIN", 4.1),
    ("TATAMOTORS", "Auto", 0.58, 0.55, 6.1, "BUY", "DOWNTREND", "LOSS", -7.3),
    ("VEDL", "Metals", 0.50, 0.60, 6.0, "BUY", "SIDEWAYS", "LOSS", -7.1),
    ("DLF", "Infra", 0.62, 0.52, 6.2, "BUY", "DOWNTREND", "LOSS", -8.4),
]


def _make_state(symbol, sector, fund, tech, judge, signal, nifty_trend, indicators):
    return {
        "fundamental_verdict": {"score": fund, "moat_strength": "MODERATE",
                                "strengths": ["demo"], "weaknesses": []},
        "technical_verdict": {"score": tech, "signal": signal, "patterns": ["HAMMER"],
                              "indicators": indicators, "weekly_trend": nifty_trend},
        "judge_verdict": {"overall_score": judge, "approved": judge >= 6.0,
                          "flags": [] if nifty_trend != "DOWNTREND" else ["FIGHTING_NIFTY"]},
        "market_context": {"nifty_trend": nifty_trend},
        "sector": sector,
        "manually_requested": False,
    }


def main() -> int:
    journal = TradingJournal()
    analyzer = RootCauseAnalyzer(journal)

    for (symbol, sector, fund, tech, judge, signal, nifty_trend, outcome, pnl_pct) in DEMO_TRADES:
        entry = 100.0
        stop = 93.0
        target = 114.0
        indicators = {"rsi_14": 57, "trend": "UPTREND" if outcome == "WIN" else "SIDEWAYS",
                      "adx_signal": "TRENDING", "obv_trend": "RISING"}
        state = _make_state(symbol, sector, fund, tech, judge, signal, nifty_trend, indicators)
        state["symbol"] = symbol
        state["technical_verdict"]["entry_price"] = entry
        state["technical_verdict"]["stop_price"] = stop
        state["technical_verdict"]["target_price"] = target

        record = journal.log_proposed(state)
        journal.log_executed(record.id, {"fill_price": entry, "broker_mode": "paper"})
        close_price = entry * (1 + pnl_pct / 100)
        closed = journal.log_closed(record.id, round(close_price, 2))
        # Mark as seeded so cold-start protection never tunes parameters on demo data.
        with Session(journal.engine) as _s:
            _rec = _s.get(type(closed), closed.id)
            _rec.is_seeded = True
            _s.add(_rec)
            _s.commit()

        snapshot = json.loads(closed.state_snapshot or "{}")
        if closed.outcome == "LOSS":
            rca = analyzer.analyze(closed, snapshot)
            console.print(f"[red]LOSS {symbol}: {rca.failure_category}[/red]")
        else:
            analyzer.analyze_win(closed, snapshot)
            console.print(f"[green]WIN {symbol}: +{pnl_pct}%[/green]")

    console.print("\n[bold]Knowledge base after seeding:[/bold]")
    for e in journal.get_active_knowledge(min_confidence=0.0):
        tag = "hypothesis" if e.is_hypothesis else "rule"
        console.print(f"  [{e.confidence:.2f}] {e.pattern_description} — seen {e.observed_count}x ({tag})")

    summary = journal.get_performance_summary()
    console.print(f"\n[cyan]Performance: {summary['total_closed']} closed | "
                  f"win rate {summary['win_rate']:.0%}[/cyan]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
