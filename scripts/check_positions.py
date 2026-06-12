"""
Daily position manager — checks open trades, advances tiered stops, closes on
stop/target, runs RCA + signal tracking, and synthesizes the day if anything closed.

    uv run python scripts/check_positions.py
"""
from __future__ import annotations

import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from src.data.fetcher import MarketDataFetcher  # noqa: E402
from src.memory.daily_synthesis import DailySynthesizer  # noqa: E402
from src.memory.journal import TradeRecord, TradingJournal  # noqa: E402
from src.memory.rca import RootCauseAnalyzer  # noqa: E402
from src.memory.reflection import PostTradeReflector  # noqa: E402
from src.risk.tiered_stops import TieredStopManager  # noqa: E402
from src.tracking.signal_tracker import SignalTracker  # noqa: E402
from src.utils import visual  # noqa: E402

console = Console()


def main() -> int:
    journal = TradingJournal()
    fetcher = MarketDataFetcher()
    reflector = PostTradeReflector(journal)
    analyzer = RootCauseAnalyzer(journal)
    stops = TieredStopManager()
    tracker = SignalTracker()

    with Session(journal.engine) as session:
        open_trades = list(session.exec(select(TradeRecord).where(TradeRecord.outcome == "OPEN")).all())

    if not open_trades:
        console.print(Panel("No open positions. All quiet. ☕", border_style="dim"))
        return 0

    closed_any = False
    for trade in open_trades:
        price = fetcher.get_current_price(trade.symbol)
        if price is None:
            console.print(f"[yellow]{trade.symbol}: price unavailable, skipping[/yellow]")
            continue

        hit_stop = trade.stop_price is not None and price <= trade.stop_price
        hit_target = trade.target_price is not None and price >= trade.target_price

        if hit_stop or hit_target:
            closed = journal.log_closed(trade.id, price)
            reflector.reflect(closed)
            snapshot = json.loads(closed.state_snapshot or "{}")
            tracker.update_from_trade(closed, snapshot)
            if closed.outcome == "LOSS":
                analyzer.analyze(closed, snapshot)
                console.print(Panel(f"🛑 {trade.symbol} stopped @ ₹{price:.2f} — capital protected.",
                                    border_style="red"))
            else:
                analyzer.analyze_win(closed, snapshot)
                console.print(Panel(f"🎉 {trade.symbol} target hit @ ₹{price:.2f}! +{closed.pnl_pct}%",
                                    border_style="green"))
            closed_any = True
            continue

        # Still holding — advance the tiered stop + show entry→target progress.
        action = stops.update_stops_for_held_position(trade, price)
        entry = trade.entry_price or price
        pnl_pct = (price - entry) / entry * 100
        tgt = trade.target_price or price
        span = (tgt - entry) or 1
        progress = max(0.0, min(1.0, (price - entry) / span))
        bar = "█" * round(progress * 10) + "░" * (10 - round(progress * 10))
        pcolor = "green" if pnl_pct >= 0 else "red"
        console.print(
            f"[bold]{trade.symbol}[/]  ₹{entry:,.0f} ──[{pcolor}]{bar}[/]── "
            f"₹{tgt:,.0f}  now ₹{price:,.0f} ([{pcolor}]{pnl_pct:+.1f}%[/])"
        )
        if action["action"] != "HOLD":
            journal.update_position(
                trade.id,
                current_stop=action["new_stop"],
                partial_exited=True if action["action"] == "PARTIAL_EXIT" else None,
            )
            console.print(f"  [cyan]{action['action']} — {action['reason']}[/cyan]")

    # Recent closed-trade outcomes as a sparkline.
    recent_closed = [
        t.pnl_pct for t in journal.get_recent(40)
        if t.outcome in ("WIN", "LOSS") and t.pnl_pct is not None
    ]
    if recent_closed:
        visual.print_pnl_sparkline(list(reversed(recent_closed)))

    if closed_any:
        entry_e = DailySynthesizer(journal).synthesize()
        console.print(Panel(entry_e.synthesis, title="📔 Daily synthesis", border_style="blue"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
