"""
Recalls relevant past experience when evaluating a new candidate.

Unlike AdaptiveThresholds (which adjusts overall strictness per regime),
PatternMatcher looks for SPECIFIC memories that resemble THIS specific
stock/setup — the way a human trader thinks "this looks like that thing
that burned me before."

Sources consulted (all knowledge categories, weighted by their own stored
confidence — no special-casing needed): HISTORICAL_REPLAY, FORCED_LEARNING,
INTRADAY_SIMULATION, SHORT_SIMULATION, and real trade RCA lessons.
"""
from __future__ import annotations

from src.memory.journal import TradingJournal


class PatternMatcher:
    def __init__(self) -> None:
        self.journal = TradingJournal()

    def recall(self, symbol: str, indicators: dict, regime: str) -> dict:
        """
        Returns: {
          adjustment: float (-1.5 to +1.5, applied to a 0-10 score),
          matches: list of {description, confidence, category},
          summary: str (one-line human-readable rationale addition)
        }
        Bounded, safe, never raises.
        """
        try:
            kb = self.journal.get_active_knowledge(min_confidence=0.0)
        except Exception:
            return self._empty()

        if not kb:
            return self._empty()

        indicators = indicators or {}
        trend = indicators.get("trend", "SIDEWAYS") or "SIDEWAYS"

        matches = []
        for e in kb:
            try:
                score = 0.0
                desc = e.pattern_description or ""

                # Strongest signal: same exact stock mentioned.
                if symbol and symbol in desc:
                    score += 0.6

                # Medium signal: same regime + trend mentioned.
                same_regime = (e.observed_in_regime == regime)
                if same_regime:
                    score += 0.25
                if trend and trend.upper() in desc.upper():
                    score += 0.15

                if score > 0.15:
                    matches.append((score * (e.confidence or 0), e))
            except Exception:
                continue

        if not matches:
            return self._empty()

        matches.sort(key=lambda x: -x[0])
        top = matches[:3]

        # Net signed adjustment: WON patterns push positive, LOST patterns push
        # negative, weighted by relevance*confidence.
        net = 0.0
        for weight, e in top:
            desc = e.pattern_description or ""
            direction = 1.0 if "WON" in desc else -1.0
            if "LOST" in desc:
                direction = -1.0
            net += direction * weight

        adjustment = max(-1.5, min(1.5, round(net * 3, 2)))

        match_list = [
            {
                "description": (e.pattern_description or "")[:80],
                "confidence": round(e.confidence or 0, 2),
                "category": e.category,
            }
            for _, e in top
        ]

        if adjustment > 0.1:
            summary = f"Memory recall: {len(top)} similar past setup(s) leaned positive (+{adjustment:.1f} adj)"
        elif adjustment < -0.1:
            summary = f"Memory recall: {len(top)} similar past setup(s) leaned negative ({adjustment:.1f} adj) — caution"
        else:
            summary = "Memory recall: no strong precedent either way"

        return {"adjustment": adjustment, "matches": match_list, "summary": summary}

    def _empty(self) -> dict:
        return {"adjustment": 0.0, "matches": [], "summary": "No relevant memory yet"}
