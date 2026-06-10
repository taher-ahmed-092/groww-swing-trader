"""
Level 3 — weekly knowledge distillation. Runs Sunday 8 PM IST.

Reads the week's daily entries + RCAs, and distills patterns that recur across
trades into the standing KnowledgeBase. Uses the stronger judge model (Sonnet) —
it runs once a week, so the quality is worth the negligible cost. Sends a Telegram
learning summary on completion.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rich.console import Console

from config.settings import settings
from src.llm import get_judge_llm, parse_json_response
from src.memory.journal import KnowledgeEntry, TradingJournal
from src.notifications.telegram_bot import TelegramNotifier

console = Console()

_SYSTEM = (
    "You are a trading system's memory curator. Your job is to identify patterns that "
    "appear across multiple trades and market days this week. Extract only patterns "
    "that are specific, actionable, and supported by evidence from this week's data. Do "
    "not repeat patterns already in the standing knowledge base unless this week added "
    "new supporting evidence. A pattern is only worth noting if it would change a future "
    "trading decision."
)


class WeeklyDistiller:
    def __init__(self, journal: TradingJournal | None = None) -> None:
        self.journal = journal or TradingJournal()
        self.notifier = TelegramNotifier()

    def _upsert_new_pattern(self, p: dict) -> None:
        pattern_id = p.get("pattern_id")
        if not pattern_id:
            return
        existing = [
            e for e in self.journal.get_active_knowledge(min_confidence=0.0)
            if e.pattern_id == pattern_id
        ]
        if existing:
            self.journal.update_knowledge_confidence(pattern_id, confirmed=True)
            return
        confidence = min(0.3, max(0.1, float(p.get("confidence", 0.1) or 0.1)))
        self.journal.log_knowledge_entry(
            KnowledgeEntry(
                pattern_id=pattern_id,
                pattern_description=p.get("description", ""),
                category=p.get("category", "MARKET_REGIME_PATTERN"),
                confidence=confidence,
                observed_count=1,
                is_hypothesis=True,
                last_seen_in_trade="weekly-distill",
                supporting_trades="[]",
            )
        )

    def distill(self) -> dict:
        daily = self.journal.get_daily_entries(last_n_days=7)
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        rcas = [r for r in self.journal.get_rcas() if r.created_at and r.created_at >= cutoff]
        knowledge = self.journal.get_active_knowledge(min_confidence=0.0)

        wins = sum(d.wins_today for d in daily)
        losses = sum(d.losses_today for d in daily)
        weekly_summary = (
            f"{len(daily)} active days, {wins}W/{losses}L, "
            f"{len(rcas)} losses analyzed this week."
        )
        focus = "Accumulate more closed trades — the knowledge base sharpens with volume."
        new_count = 0
        confirmed_ids: list[str] = []
        contradicted_ids: list[str] = []

        if settings.has_anthropic_key and daily:
            prompt = (
                "Daily synthesis texts this week:\n"
                + "\n".join(f"- {d.date} [{d.market_regime}]: {d.synthesis}" for d in daily)
                + "\n\nRCA summaries this week:\n"
                + "\n".join(
                    f"- {r.symbol}: {r.failure_category} — {r.actionable_lesson}" for r in rcas
                )
                + "\n\nCurrent standing knowledge (do not duplicate):\n"
                + "\n".join(f"- [{e.pattern_id}] {e.pattern_description}" for e in knowledge)
                + f"\n\nWeek stats: {wins}W / {losses}L\n\n"
                "Respond ONLY with JSON:\n"
                "{\n"
                '  "new_patterns": [{"pattern_id": "<slug>", "description": "<str>",\n'
                '     "category": "SECTOR_PATTERN|INDICATOR_PATTERN|TIMING_PATTERN|'
                'FUNDAMENTAL_PATTERN|MARKET_REGIME_PATTERN",\n'
                '     "confidence": <0.1-0.3>, "evidence_from_this_week": "<str>"}],\n'
                '  "confirmed_patterns": [<pattern_id seen again this week>],\n'
                '  "contradicted_patterns": [<pattern_id contradicted this week>],\n'
                '  "weekly_summary": "<100 words: overall progress>",\n'
                '  "focus_for_next_week": "<what to watch or improve>"\n'
                "}"
            )
            try:
                resp = get_judge_llm(temperature=0).invoke(
                    [("system", _SYSTEM), ("human", prompt)]
                )
                parsed = parse_json_response(getattr(resp, "content", "") or "")
            except Exception as exc:
                console.print(f"[yellow]Weekly distillation LLM call failed: {exc}[/yellow]")
                parsed = {}

            # Learning quality gate: if EVERY loss this week was market noise
            # (MARKET_EVENT / SECTOR_HEADWIND / MARKET_REGIME), don't let those
            # contradictions punish otherwise-good patterns.
            from src.memory.rca import SIGNAL_CATEGORIES

            week_has_signal_loss = any(r.failure_category in SIGNAL_CATEGORIES for r in rcas)

            if parsed:
                for p in parsed.get("new_patterns", []) or []:
                    self._upsert_new_pattern(p)
                    new_count += 1
                for pid in parsed.get("confirmed_patterns", []) or []:
                    if self.journal.update_knowledge_confidence(pid, confirmed=True):
                        confirmed_ids.append(pid)
                if week_has_signal_loss:
                    for pid in parsed.get("contradicted_patterns", []) or []:
                        if self.journal.update_knowledge_confidence(pid, confirmed=False):
                            contradicted_ids.append(pid)
                elif parsed.get("contradicted_patterns"):
                    console.print(
                        "[dim]Skipping contradictions — all losses this week were noise.[/dim]"
                    )
                weekly_summary = parsed.get("weekly_summary", weekly_summary)
                focus = parsed.get("focus_for_next_week", focus)

        total_active = len(self.journal.get_active_knowledge(min_confidence=0.0))
        high_confidence = len(self.journal.get_active_knowledge(min_confidence=0.7))

        self.notifier.send_message(
            "📚 Weekly Learning Summary\n"
            f"{weekly_summary}\n"
            f"Knowledge base: {total_active} patterns | {high_confidence} high confidence\n"
            f"Focus next week: {focus}"
        )

        return {
            "new_patterns": new_count,
            "confirmed_patterns": confirmed_ids,
            "contradicted_patterns": contradicted_ids,
            "total_active": total_active,
            "high_confidence": high_confidence,
            "weekly_summary": weekly_summary,
            "focus_for_next_week": focus,
        }
