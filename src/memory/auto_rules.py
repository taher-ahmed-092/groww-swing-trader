"""
Reads the knowledge base and automatically extracts trading rules. These
rules are applied in the scout WITHOUT requiring human intervention.

This is the real self-improvement loop:
1. Historical replay finds patterns
2. Auto-rules extracts them into actionable rules
3. Scout applies them to new candidates
4. Outcomes confirm or refute the rules
5. Knowledge base updates
6. Auto-rules re-extracts (runs nightly)
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from src.memory.journal import TradingJournal

AUTO_RULES_FILE = Path("data/cache/auto_rules.json")


class AutoRuleExtractor:
    MIN_CONFIDENCE = 0.75
    MIN_OBSERVATIONS = 10

    def extract_and_save(self) -> dict:
        """
        Reads knowledge base. Generates:
        - stock_vetoes: {symbol: reason} — never trade this stock
        - stock_boosts: {symbol: score_bonus} — prefer this stock
        - setup_vetoes: list of {rsi_min, rsi_max, adx_signal, trend, reason}
        - setup_boosts: list of {conditions}
        Saves to AUTO_RULES_FILE. Returns summary.
        """
        journal = TradingJournal()
        kb = journal.get_active_knowledge(min_confidence=0.0)

        stock_vetoes: dict = {}
        stock_boosts: dict = {}
        setup_vetoes: list = []
        setup_boosts: list = []

        for e in kb:
            if e.confidence < self.MIN_CONFIDENCE:
                continue
            if e.observed_count < self.MIN_OBSERVATIONS:
                continue

            desc = e.pattern_description or ""
            is_loss = "LOST" in desc or "LOSS" in desc
            is_win = not is_loss and ("WON" in desc or "WIN" in desc)

            if not (is_loss or is_win):
                continue

            symbol = self._extract_symbol(desc)
            rsi = self._extract_rsi(desc)
            trend = self._extract_trend(desc)
            adx = self._extract_adx(desc)

            if is_loss and e.confidence >= 0.85:
                if symbol:
                    existing = stock_vetoes.get(symbol, {})
                    existing["reason"] = f"[{e.confidence:.0%}] {desc[:60]}"
                    existing["confidence"] = e.confidence
                    stock_vetoes[symbol] = existing

                if rsi is not None and trend and adx:
                    setup_vetoes.append({
                        "rsi_min": rsi - 3, "rsi_max": rsi + 3,
                        "trend": trend, "adx_signal": adx,
                        "reason": f"[{e.confidence:.0%}] {desc[:50]}",
                        "confidence": e.confidence,
                    })

            elif is_win and e.confidence >= 0.80:
                if symbol:
                    stock_boosts[symbol] = {
                        "bonus": min(1.5, e.confidence * 2),
                        "reason": f"[{e.confidence:.0%}] {desc[:60]}",
                    }
                if rsi is not None and trend and adx:
                    setup_boosts.append({
                        "rsi_min": rsi - 5, "rsi_max": rsi + 5,
                        "trend": trend, "adx_signal": adx,
                        "bonus": min(1.0, e.confidence * 1.5),
                        "reason": f"[{e.confidence:.0%}] {desc[:50]}",
                    })

        # Seed rule from analysis: RSI 58-65 + ADX CHOPPY consistently loses
        # (LESSONS.md analysis) — overridden as more specific evidence accrues.
        if not any(v.get("rsi_min") == 57 for v in setup_vetoes):
            setup_vetoes.append({
                "rsi_min": 57, "rsi_max": 66, "adx_signal": "CHOPPY",
                "reason": "Empirical: elevated RSI + choppy ADX consistently underperforms",
                "confidence": 0.75,
            })

        rules = {
            "stock_vetoes": stock_vetoes,
            "stock_boosts": stock_boosts,
            "setup_vetoes": setup_vetoes,
            "setup_boosts": setup_boosts,
            "generated_at": datetime.now().isoformat(),
            "total_rules": (len(stock_vetoes) + len(stock_boosts)
                           + len(setup_vetoes) + len(setup_boosts)),
        }

        AUTO_RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
        AUTO_RULES_FILE.write_text(json.dumps(rules, indent=2))
        return rules

    @staticmethod
    def load() -> dict:
        if AUTO_RULES_FILE.exists():
            try:
                return json.loads(AUTO_RULES_FILE.read_text())
            except Exception:
                pass
        return {"stock_vetoes": {}, "stock_boosts": {}, "setup_vetoes": [], "setup_boosts": []}

    @staticmethod
    def _extract_symbol(desc: str) -> str | None:
        from src.data.watchlist import ALL_STOCKS

        for sym in ALL_STOCKS:
            if sym in desc:
                return sym
        return None

    @staticmethod
    def _extract_rsi(desc: str) -> float | None:
        m = re.search(r"RSI (\d+\.?\d*)", desc)
        return float(m.group(1)) if m else None

    @staticmethod
    def _extract_trend(desc: str) -> str | None:
        for t in ("UPTREND", "DOWNTREND", "SIDEWAYS"):
            if t in desc.upper():
                return t
        return None

    @staticmethod
    def _extract_adx(desc: str) -> str | None:
        for a in ("TRENDING", "CHOPPY", "NEUTRAL"):
            if f"ADX {a}" in desc.upper():
                return a
        return None
