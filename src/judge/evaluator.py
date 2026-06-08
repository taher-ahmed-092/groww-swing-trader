"""
LLM-as-judge — a skeptical senior trader that protects capital.

Scores each proposed trade against fundamentals, technicals, and risk/reward. Can veto.
Works in paper mode with no API key (returns a mock approval flagged NO_API_KEY).
"""
from __future__ import annotations

from rich.console import Console

from config.settings import settings
from src.llm import get_llm, parse_json_response
from src.orchestrator.state import TradeState

console = Console()

_SYSTEM = (
    "You are a skeptical senior trader evaluating a proposed swing trade.\n"
    "Your job is to protect capital, not find reasons to trade.\n"
    "Be critical. Approve only when multiple independent signals align and "
    "risk/reward is clearly favorable.\n"
    "Flag any sign of: emotional trading, FOMO, insufficient evidence, poor "
    "risk/reward, or over-confidence."
)

_VALID_FLAGS = {
    "EMOTIONAL_TRADE_SUSPECTED", "LOW_CONVICTION", "POOR_RISK_REWARD",
    "FUNDAMENTAL_WEAK", "TECHNICAL_CONFLICT", "MANUALLY_REQUESTED", "NO_API_KEY",
}


class LLMJudge:
    def evaluate(self, state: TradeState) -> dict:
        fundamental = state.get("fundamental_verdict", {})
        technical = state.get("technical_verdict", {})
        manually_requested = bool(state.get("manually_requested"))

        entry = technical.get("entry_price") or 0
        stop = technical.get("stop_price") or 0
        target = technical.get("target_price") or 0
        rr = round((target - entry) / (entry - stop), 4) if (entry - stop) else None

        if not settings.has_anthropic_key:
            flags = ["NO_API_KEY"]
            if manually_requested:
                flags.append("MANUALLY_REQUESTED")
            return {
                "approved": True,
                "score": 0.5,
                "reasoning": "MOCK — no API key",
                "flags": flags,
            }

        prompt = (
            f"Fundamental score: {fundamental.get('score')} | proceed={fundamental.get('proceed')}\n"
            f"Fundamental key findings: strengths={fundamental.get('strengths')} "
            f"weaknesses={fundamental.get('weaknesses')} moat={fundamental.get('moat')}\n\n"
            f"Technical score: {technical.get('score')} | signal={technical.get('signal')}\n"
            f"Entry={entry} Stop={stop} Target={target}\n"
            f"Risk/Reward ratio: {rr}\n\n"
            f"Manually requested by user: {manually_requested}\n\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "approved": <bool>,\n'
            '  "score": <float 0-1>,\n'
            '  "reasoning": "<concise>",\n'
            f'  "flags": [<subset of {sorted(_VALID_FLAGS)}>]\n'
            "}"
        )

        try:
            resp = get_llm(temperature=0).invoke(
                [("system", _SYSTEM), ("human", prompt)]
            )
            verdict = parse_json_response(getattr(resp, "content", "") or "")
        except Exception as exc:
            console.print(f"[yellow]Judge LLM call failed: {exc}[/yellow]")
            verdict = {}

        if not verdict:
            verdict = {
                "approved": False,
                "score": 0.0,
                "reasoning": "Judge response unparseable; vetoing for safety.",
                "flags": ["LOW_CONVICTION"],
            }

        # Normalize flags and always surface MANUALLY_REQUESTED.
        flags = [f for f in verdict.get("flags", []) if f in _VALID_FLAGS]
        if manually_requested and "MANUALLY_REQUESTED" not in flags:
            flags.append("MANUALLY_REQUESTED")
        verdict["flags"] = flags
        verdict["approved"] = bool(verdict.get("approved"))
        return verdict
