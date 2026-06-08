"""
Fundamental agent.

Code computes the ratios (ratios.py). The LLM only INTERPRETS the numbers it is
handed — it never sees raw yfinance dicts and never computes ratios itself
(CLAUDE.md rule 4).
"""
from __future__ import annotations

from rich.console import Console

from config.settings import settings
from src.agents.fundamental.ratios import compute_ratios
from src.data.fetcher import MarketDataFetcher
from src.llm import get_llm, parse_json_response
from src.orchestrator.state import TradeState

console = Console()

_SYSTEM = (
    "You are an equity fundamental analyst. You are given pre-computed financial "
    "ratios for a single company. Interpret them qualitatively. You MUST NOT invent "
    "or recompute any numbers — reason only from the values provided."
)


class FundamentalAgent:
    def __init__(self) -> None:
        self.fetcher = MarketDataFetcher()

    def analyze(self, state: TradeState) -> dict:
        symbol = state["symbol"]
        info = self.fetcher.get_fundamentals(symbol) or {}
        ratios = compute_ratios(info)

        if not settings.has_anthropic_key:
            return {
                "score": 0.5,
                "strengths": [],
                "weaknesses": [],
                "swot": {},
                "moat": "unknown",
                "proceed": True,
                "reasoning": "MOCK — no ANTHROPIC_API_KEY; ratios computed but not interpreted.",
                "ratios": ratios,
            }

        prompt = (
            f"Company: {symbol}\n"
            f"Computed ratios (numbers are authoritative, do not change them):\n{ratios}\n\n"
            "Respond ONLY with JSON of the form:\n"
            "{\n"
            '  "score": <float 0-1, fundamental quality>,\n'
            '  "strengths": [<str>],\n'
            '  "weaknesses": [<str>],\n'
            '  "swot": {"strengths": [], "weaknesses": [], "opportunities": [], "threats": []},\n'
            '  "moat": "<one-line moat assessment>",\n'
            '  "proceed": <bool>,\n'
            '  "reasoning": "<concise rationale>"\n'
            "}"
        )

        try:
            resp = get_llm(temperature=0).invoke(
                [("system", _SYSTEM), ("human", prompt)]
            )
            verdict = parse_json_response(getattr(resp, "content", "") or "")
        except Exception as exc:
            console.print(f"[yellow]Fundamental LLM call failed: {exc}[/yellow]")
            verdict = {}

        if not verdict:
            verdict = {
                "score": 0.5, "strengths": [], "weaknesses": [], "swot": {},
                "moat": "unknown", "proceed": False,
                "reasoning": "LLM response unparseable; defaulting to no-proceed.",
            }
        verdict["ratios"] = ratios
        return verdict
