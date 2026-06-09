"""
Level 2 — daily synthesis. Runs 4:30 PM IST each trading day.

Reads today's proposals, executions, closures and RCAs, then writes one honest
DailyJournalEntry. The tone is a disciplined trader reviewing the day objectively:
what was learned, not what was earned.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from rich.console import Console
from sqlmodel import Session, select

from config.settings import settings
from src.data.market_context import MarketContext
from src.llm import get_llm, parse_json_response
from src.memory.journal import (
    DailyJournalEntry,
    RootCauseRecord,
    TradeRecord,
    TradingJournal,
)

console = Console()

_SYSTEM = (
    "You are a trading journal writer for a systematic AI swing trader. Write a "
    "concise, honest daily entry. Focus on what was learned, not what was earned. Be "
    "specific about what indicators worked or failed. Use the tone of a disciplined "
    "trader reviewing their day objectively."
)


def _on_date(dt, date: str) -> bool:
    return bool(dt) and dt.date().isoformat() == date


class DailySynthesizer:
    def __init__(self, journal: TradingJournal | None = None) -> None:
        self.journal = journal or TradingJournal()
        self.market = MarketContext()

    def _market_regime(self) -> str:
        try:
            return self.market.get_nifty_context().get("nifty_trend", "UNKNOWN")
        except Exception:
            return "UNKNOWN"

    def synthesize(self, date: str | None = None) -> DailyJournalEntry:
        date = date or datetime.now(timezone.utc).date().isoformat()

        with Session(self.journal.engine) as session:
            all_trades = list(session.exec(select(TradeRecord)).all())
            all_rcas = list(session.exec(select(RootCauseRecord)).all())

        proposed = [t for t in all_trades if _on_date(t.proposed_at, date)]
        executed = [t for t in all_trades if _on_date(t.executed_at, date)]
        closed = [t for t in all_trades if _on_date(t.closed_at, date)]
        wins = [t for t in closed if t.outcome == "WIN"]
        losses = [t for t in closed if t.outcome == "LOSS"]
        total_pnl = round(sum(t.pnl or 0.0 for t in closed), 4)
        rcas_today = [r for r in all_rcas if _on_date(r.created_at, date)]
        regime = self._market_regime()

        base = dict(
            date=date,
            market_regime=regime,
            trades_proposed=len(proposed),
            trades_executed=len(executed),
            trades_closed_today=len(closed),
            wins_today=len(wins),
            losses_today=len(losses),
            total_pnl_today=total_pnl,
        )

        # No activity: still record the regime for the historical series.
        if not proposed and not closed:
            entry = DailyJournalEntry(
                **base,
                patterns_observed="[]",
                synthesis=f"No trading activity today. Market: {regime}.",
                lessons_extracted="[]",
            )
            return self.journal.log_daily_entry(entry)

        # Default (no-LLM) synthesis so paper mode still produces a useful entry.
        rca_lessons = [r.actionable_lesson for r in rcas_today if r.actionable_lesson]
        synthesis = (
            f"Market {regime}. Proposed {len(proposed)}, executed {len(executed)}, "
            f"closed {len(closed)} ({len(wins)}W/{len(losses)}L), P&L ₹{total_pnl}."
        )
        patterns = sorted({c.signal for c in proposed if c.signal})
        lessons = rca_lessons

        if settings.has_anthropic_key:
            closed_brief = [
                {"symbol": t.symbol, "outcome": t.outcome, "pnl_pct": t.pnl_pct}
                for t in closed
            ]
            rca_brief = [
                {"symbol": r.symbol, "category": r.failure_category, "lesson": r.actionable_lesson}
                for r in rcas_today
            ]
            prompt = (
                f"Date: {date} | Market regime: {regime}\n"
                f"Proposed: {len(proposed)} | Executed: {len(executed)} | "
                f"Closed: {len(closed)} ({len(wins)}W/{len(losses)}L) | P&L: ₹{total_pnl}\n"
                f"Closed trades: {closed_brief}\n"
                f"RCA summaries (losses): {rca_brief}\n\n"
                "Respond ONLY with JSON:\n"
                "{\n"
                '  "synthesis": "<150-200 word daily narrative>",\n'
                '  "patterns_observed": [<specific patterns seen today>],\n'
                '  "lessons_extracted": [<concrete lessons for tomorrow>]\n'
                "}"
            )
            try:
                resp = get_llm(temperature=0).invoke([("system", _SYSTEM), ("human", prompt)])
                parsed = parse_json_response(getattr(resp, "content", "") or "")
            except Exception as exc:
                console.print(f"[yellow]Daily synthesis LLM call failed: {exc}[/yellow]")
                parsed = {}
            if parsed:
                synthesis = parsed.get("synthesis", synthesis)
                patterns = parsed.get("patterns_observed", patterns)
                lessons = parsed.get("lessons_extracted", lessons)

        entry = DailyJournalEntry(
            **base,
            patterns_observed=json.dumps(patterns, default=str),
            synthesis=synthesis,
            lessons_extracted=json.dumps(lessons, default=str),
        )
        return self.journal.log_daily_entry(entry)
