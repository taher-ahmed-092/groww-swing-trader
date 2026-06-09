"""
Gut check — the experienced trader's intuition.

Runs AFTER fundamental + technical + judge with all verdicts in hand, and asks the
holistic question: "Does this feel right?" It can flag concerns a checklist misses.
Falls back to a rule-based composite in DEMO mode / without an API key.
"""
from __future__ import annotations

import json

from rich.console import Console

from config.settings import settings

console = Console()

_SYSTEM = (
    "You are a veteran Indian stock market trader with 20 years of NSE/BSE experience. "
    "You have seen bull markets, bear markets, flash crashes, and everything in between. "
    "You know how Indian retail investors, FIIs, and DIIs behave. You can sense when "
    "something's off even when all the numbers look fine.\n\n"
    "Your job is to do a final gut check on a proposed trade. You've already seen the "
    "systematic analysis. Now look at it HOLISTICALLY. Trust your experience.\n\n"
    "You are NOT looking for reasons to approve — you're looking for anything that "
    "doesn't add up. A trade that passes all checks but doesn't feel right should get a "
    "low gut score. Be specific. Point to the exact thing bothering you."
)


class GutCheckAgent:
    def check(self, state: dict) -> dict:
        if settings.effective_demo_mode or not settings.has_anthropic_key:
            return self._rule_based_gut(state)

        symbol = state.get("symbol", "unknown")
        fund = state.get("fundamental_verdict", {})
        tech = state.get("technical_verdict", {})
        judge = state.get("judge_verdict", {})
        sentiment = state.get("sentiment", {})
        lessons = state.get("lessons", "")
        knowledge = state.get("knowledge_context", "")
        market = state.get("market_context", {})
        indics = tech.get("indicators", {})

        user = f"""Gut check for {symbol}:

FUNDAMENTAL: Score {fund.get('score', 0):.2f}/1.0 | Moat: {fund.get('moat_strength', '?')}
Key strengths: {fund.get('strengths', [])}
Key concerns: {fund.get('weaknesses', [])}

TECHNICAL: Signal {tech.get('signal', '?')} | Score {tech.get('score', 0):.2f}/1.0
Trend: {indics.get('trend', '?')} | RSI: {indics.get('rsi_14', 0)}
ADX: {indics.get('adx_signal', '?')} | OBV: {indics.get('obv_trend', '?')}
Candlestick: {tech.get('patterns', ['NONE'])} | Weekly trend: {tech.get('weekly_trend', '?')}
Entry recommendation: {state.get('entry_recommendation', {}).get('entry_rationale', '?')}

JUDGE: {judge.get('overall_score', 0):.1f}/10 | Verdict: {judge.get('one_line_verdict', '?')}
Flags: {judge.get('flags', [])}

MARKET: Nifty {market.get('nifty_trend', '?')} | regime {market.get('regime', '?')}
Sentiment: {sentiment.get('sentiment_label', 'NEUTRAL')} | Events: {sentiment.get('key_events', [])}

RELEVANT EXPERIENCE:
{lessons or "No relevant past trades for this setup."}

LEARNED PATTERNS:
{knowledge or "Knowledge base empty — early trading phase."}

MANUALLY REQUESTED: {state.get('manually_requested', False)}

Given all of this — what does your gut say? Return JSON:
{{
  "gut_score": <0-10, 10 = take immediately>,
  "gut_verdict": "STRONG_YES"|"YES"|"NEUTRAL"|"NO"|"STRONG_NO",
  "gut_reasoning": "<one honest paragraph>",
  "gut_concerns": [<specific concern>],
  "gut_positives": [<what looks good>],
  "modifies_decision": <true if you'd override the judge>,
  "override_reason": "<if modifies_decision, why>"
}}"""

        try:
            from anthropic import Anthropic

            client = Anthropic(api_key=settings.anthropic_api_key)
            response = client.messages.create(
                model=settings.llm_model_default,  # Haiku — runs every trade
                max_tokens=600,
                system=_SYSTEM,
                messages=[{"role": "user", "content": user}],
            )
            content = response.content[0].text.replace("```json", "").replace("```", "").strip()
            result = json.loads(content)
            result.setdefault("gut_score", 5.0)
            result.setdefault("gut_verdict", "NEUTRAL")
            result.setdefault("gut_reasoning", "")
            result.setdefault("gut_concerns", [])
            result.setdefault("gut_positives", [])
            result.setdefault("modifies_decision", False)
            result.setdefault("override_reason", "")
            return result
        except Exception as exc:
            console.print(f"[yellow]Gut check failed: {exc}[/yellow]")
            return self._rule_based_gut(state)

    def _rule_based_gut(self, state: dict) -> dict:
        fund_score = state.get("fundamental_verdict", {}).get("score", 0) or 0
        tech_score = state.get("technical_verdict", {}).get("score", 0) or 0
        judge_score = (state.get("judge_verdict", {}).get("overall_score", 5) or 5) / 10
        manually = state.get("manually_requested", False)
        flags = state.get("judge_verdict", {}).get("flags", [])

        gut_score = (fund_score + tech_score + judge_score) / 3 * 10

        concerns = []
        if manually:
            concerns.append("Trade was manually requested — potential emotional bias")
        if "POOR_RISK_REWARD" in str(flags):
            concerns.append("Risk/reward looks unattractive")
        if state.get("market_context", {}).get("nifty_trend") == "DOWNTREND":
            concerns.append("Fighting the overall market trend")

        return {
            "gut_score": round(gut_score, 1),
            "gut_verdict": "YES" if gut_score >= 7 else "NEUTRAL" if gut_score >= 5 else "NO",
            "gut_reasoning": "Rule-based gut check (no API key). Based on composite score.",
            "gut_concerns": concerns,
            "gut_positives": ["Systematic analysis approved this setup"],
            "modifies_decision": False,
            "override_reason": "",
        }
