"""
Lessons retrieval — turns the trade journal into context for future decisions.

Relevant past reflections are injected into the fundamental/technical/judge prompts
("What the system has learned:"), and aggregate failure patterns are fed to the
scout so it can avoid historically losing setups.
"""
from __future__ import annotations

import json
from collections import Counter

from sqlmodel import Session, select

from src.memory.journal import RootCauseRecord, TradeRecord, TradingJournal


class LessonsRetriever:
    def __init__(self, journal: TradingJournal | None = None) -> None:
        self.journal = journal or TradingJournal()

    def _closed_with_reflection(self) -> list[TradeRecord]:
        with Session(self.journal.engine) as session:
            stmt = (
                select(TradeRecord)
                .where(TradeRecord.outcome != "OPEN")
                .where(TradeRecord.reflection.is_not(None))
                .order_by(TradeRecord.id.desc())
            )
            return list(session.exec(stmt).all())

    def get_relevant_lessons(
        self, symbol: str, sector: str | None = None, signal: str | None = None
    ) -> str:
        records = self._closed_with_reflection()
        if not records:
            return ""

        def relevance(r: TradeRecord) -> int:
            score = 0
            if r.symbol == symbol:
                score += 100  # exact symbol = highest relevance
            if sector and r.sector and r.sector == sector:
                score += 10
            if signal and r.signal and r.signal == signal:
                score += 5
            return score

        scored = [(relevance(r), r) for r in records]
        # Keep only records with some relevance; fall back to recency if none match.
        relevant = [r for s, r in sorted(scored, key=lambda x: x[0], reverse=True) if s > 0]
        if not relevant:
            return ""

        top = relevant[:3]
        lines = ["PAST LESSONS (most relevant to current setup):"]
        for i, r in enumerate(top, 1):
            date = r.closed_at.date().isoformat() if r.closed_at else "n/a"
            lines.append(f"[{i}] {r.symbol} {date} {r.outcome}: {r.reflection}")
        return "\n".join(lines)

    def get_recent_rca_context(
        self, symbol: str, sector: str | None = None, n: int = 3
    ) -> str:
        """Block C — recent root-cause lessons for this symbol or sector."""
        with Session(self.journal.engine) as session:
            rcas = list(
                session.exec(
                    select(RootCauseRecord).order_by(RootCauseRecord.id.desc())
                ).all()
            )
            sectors: dict[int, str | None] = {}
            trade_ids = [r.trade_id for r in rcas]
            if trade_ids:
                trades = session.exec(
                    select(TradeRecord).where(TradeRecord.id.in_(trade_ids))
                ).all()
                sectors = {t.id: t.sector for t in trades}

        relevant: list[RootCauseRecord] = []
        for r in rcas:
            if r.symbol == symbol or (sector and sectors.get(r.trade_id) == sector):
                relevant.append(r)
            if len(relevant) >= n:
                break
        if not relevant:
            return ""

        lines = ["RECENT LOSSES IN THIS SECTOR/SYMBOL:"]
        for r in relevant:
            date = r.created_at.date().isoformat() if r.created_at else "n/a"
            lines.append(f"[{date}] {r.symbol}: {r.failure_category} — {r.actionable_lesson}")
        return "\n".join(lines)

    def get_failure_patterns(self) -> list[str]:
        with Session(self.journal.engine) as session:
            stmt = select(TradeRecord).where(TradeRecord.outcome == "LOSS")
            losses = list(session.exec(stmt).all())

        if not losses:
            return []

        flag_counts: Counter[str] = Counter()
        for r in losses:
            try:
                flags = json.loads(r.judge_flags) if r.judge_flags else []
            except (json.JSONDecodeError, TypeError):
                flags = []
            for f in flags:
                flag_counts[f] += 1

        total = len(losses)
        top = flag_counts.most_common(3)
        return [
            f"{flag} appeared in {round(count / total * 100)}% of losses"
            for flag, count in top
        ]
