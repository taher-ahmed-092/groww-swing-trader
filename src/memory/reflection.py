"""
Post-trade reflection. After a position closes, the LLM critiques the original
thesis vs the actual outcome and the lesson is saved back to the journal so it can
be retrieved as context for future trades.
"""
from __future__ import annotations

from rich.console import Console

from config.settings import settings
from src.llm import get_llm
from src.memory.journal import TradeRecord, TradingJournal

console = Console()

_SYSTEM = (
    "You are a trading coach reviewing a closed swing trade. Be specific and concise."
)


class PostTradeReflector:
    def __init__(self, journal: TradingJournal | None = None) -> None:
        self.journal = journal or TradingJournal()

    def reflect(self, trade: TradeRecord) -> str:
        if not settings.has_anthropic_key:
            reflection = (
                f"PLACEHOLDER — no ANTHROPIC_API_KEY. {trade.symbol} closed "
                f"{trade.outcome} (pnl_pct={trade.pnl_pct})."
            )
            if trade.id is not None:
                self.journal.log_reflection(trade.id, reflection)
            return reflection

        prompt = (
            f"Symbol: {trade.symbol}\n"
            f"Thesis confidence: {trade.confidence}, judge score: {trade.judge_score}\n"
            f"Entry: {trade.entry_price}  Stop: {trade.stop_price}  Target: {trade.target_price}\n"
            f"Outcome: {trade.outcome}  pnl: {trade.pnl}  pnl_pct: {trade.pnl_pct}\n\n"
            "What did the system get right? What did it miss? What would improve the "
            "prediction next time? Be specific and concise — 3-5 sentences max."
        )

        try:
            resp = get_llm(temperature=0).invoke(
                [("system", _SYSTEM), ("human", prompt)]
            )
            reflection = (getattr(resp, "content", "") or "").strip()
        except Exception as exc:
            console.print(f"[yellow]Reflection LLM call failed: {exc}[/yellow]")
            reflection = f"Reflection unavailable ({exc})."

        if trade.id is not None:
            self.journal.log_reflection(trade.id, reflection)
        return reflection
