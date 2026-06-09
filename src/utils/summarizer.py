"""
Plain-English trade summarizer.

Tries the free HuggingFace inference API (facebook/bart-large-cnn, no auth) and
falls back to a deterministic rule-based summary. Answers "should I take this
trade?" in 2-3 sentences. Never raises.
"""
from __future__ import annotations

import httpx

_HF_URL = "https://api-inference.huggingface.co/models/facebook/bart-large-cnn"


class TradeSummarizer:
    def rule_based_summary(self, state: dict) -> str:
        symbol = state.get("symbol", "this stock")
        fund = state.get("fundamental_verdict", {})
        tech = state.get("technical_verdict", {})
        judge = state.get("judge_verdict", {})
        gut = state.get("gut_check", {})

        approved = judge.get("approved")
        signal = tech.get("signal", "?")
        fscore = fund.get("score", 0) or 0
        tscore = tech.get("score", 0) or 0
        jscore = judge.get("overall_score", 0) or 0

        verdict = "a BUY" if approved else "NOT a trade right now"
        lead = (
            f"{symbol} looks like {verdict}: technicals say {signal} "
            f"(fundamental {fscore:.0%}, technical {tscore:.0%}, judge {jscore:.1f}/10)."
        )
        if approved:
            body = (
                f"The setup cleared every gate including the gut check "
                f"({gut.get('gut_verdict', 'N/A')}). Size it per the Kelly recommendation "
                "and respect the stop."
            )
        else:
            flags = judge.get("flags", []) or []
            reason = flags[0] if flags else "insufficient conviction"
            body = f"It was held back by: {reason}. Wait for a cleaner setup."
        return f"{lead} {body}"

    def summarize(self, state: dict) -> str:
        # Build the deterministic answer first — it's also the fallback.
        base = self.rule_based_summary(state)
        try:
            resp = httpx.post(_HF_URL, json={"inputs": base,
                                             "parameters": {"max_length": 80, "min_length": 20}},
                              timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and data and data[0].get("summary_text"):
                    return data[0]["summary_text"].strip()
        except Exception:
            pass
        return base
