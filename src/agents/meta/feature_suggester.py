"""
Feature suggester — the system proposes its own evolution.

Monthly, it reads the journal + knowledge base + performance and asks an LLM (Haiku,
tight prompt) for 3 concrete new-feature ideas. Without an API key it returns a fixed
set of seed suggestions. Never raises.
"""
from __future__ import annotations

import json
import os
from collections import Counter

from rich.console import Console

from config.settings import settings
from src.analytics.performance import PerformanceAnalyzer
from src.memory.journal import TradingJournal

console = Console()

_SUGGESTIONS_FILE = os.path.join("data", "cache", "feature_suggestions.json")

_SEED = [
    {
        "title": "Earnings Surprise Momentum",
        "description": ("Stocks that beat earnings by >10% last quarter have higher "
                        "follow-through. Add as a +1 scout scoring factor."),
        "effort": "LOW", "expected_impact": "Improves scout quality",
    },
    {
        "title": "FII/DII Flow Divergence Alert",
        "description": ("When FIIs sell but DIIs buy aggressively, it often signals a "
                        "bottoming pattern. Flag as a high-conviction setup."),
        "effort": "MED", "expected_impact": "New high-conviction setup type",
    },
    {
        "title": "Pre-Announcement Silence Detection",
        "description": ("Companies often go quiet on filings 2-3 days before major "
                        "announcements. Detect unusual silence as a risk flag."),
        "effort": "MED", "expected_impact": "Reduces earnings-surprise losses",
    },
]


class FeatureSuggester:
    SUGGESTIONS_FILE = _SUGGESTIONS_FILE

    def __init__(self, journal: TradingJournal | None = None) -> None:
        self.journal = journal or TradingJournal()

    def _seed_suggestions(self) -> list[dict]:
        return [dict(s) for s in _SEED]

    def _top_failure(self, closed) -> str:
        failures = [
            t.failure_category for t in closed
            if getattr(t, "failure_category", None) and t.outcome == "LOSS"
        ]
        if not failures:
            return "unknown"
        return Counter(failures).most_common(1)[0][0]

    def _save(self, suggestions: list[dict]) -> None:
        try:
            os.makedirs(os.path.dirname(self.SUGGESTIONS_FILE), exist_ok=True)
            with open(self.SUGGESTIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(suggestions, f, indent=2)
        except OSError:
            pass

    def run(self) -> list[dict]:
        if not settings.has_anthropic_key or settings.effective_demo_mode:
            return self._seed_suggestions()

        closed = self.journal.get_recent(n=50)
        kb = self.journal.get_active_knowledge(min_confidence=0.0)
        summary = PerformanceAnalyzer(self.journal).get_summary()
        top_kb = kb[0].pattern_description[:60] if kb else "none"

        context = (
            "Trading system state:\n"
            f"Win rate: {summary.get('win_rate', 0):.0%}\n"
            f"Total trades: {summary.get('total_trades', 0)}\n"
            f"Most common failure: {self._top_failure(closed)}\n"
            f"Knowledge patterns: {len(kb)} (top: {top_kb})\n"
            "Current data sources: yfinance, screener.in, Finnhub, Google News RSS, NSE bulk deals\n"
            "Current indicators: RSI, MACD, ADX, Ichimoku, VWAP, Pivots, CMF, Supertrend"
        )
        system = (
            "You are a trading systems architect. Suggest 3 specific new features that "
            "would improve this Indian swing trading system. Be concrete — no vague "
            "advice. Focus on features that directly improve edge or risk management. "
            "Return a JSON array of 3 objects: "
            "[{title, description, effort: LOW/MED/HIGH, expected_impact}]"
        )
        try:
            from anthropic import Anthropic

            client = Anthropic(api_key=settings.anthropic_api_key)
            resp = client.messages.create(
                model=settings.llm_model_default, max_tokens=400,
                system=system, messages=[{"role": "user", "content": context}],
            )
            text = resp.content[0].text.replace("```json", "").replace("```", "").strip()
            suggestions = json.loads(text)
            if isinstance(suggestions, list) and suggestions:
                self._save(suggestions)
                return suggestions
        except Exception as exc:
            console.print(f"[yellow]Feature suggester failed: {exc}[/yellow]")
        return self._seed_suggestions()
