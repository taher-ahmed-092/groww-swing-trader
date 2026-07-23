"""
Root Cause Analysis — runs on every closed LOSS before daily synthesis.

A fast heuristic pre-classifies the failure; an optional LLM pass (Haiku) refines it
and proposes a standing knowledge pattern. Wins instead confirm any standing patterns
that were present at entry — that's how a hypothesis graduates into a confident rule.
"""
from __future__ import annotations

import json
from datetime import datetime

from rich.console import Console

from config.risk_limits import LIMITS
from config.settings import settings
from src.llm import get_llm, parse_json_response
from src.memory.journal import KnowledgeEntry, RootCauseRecord, TradeRecord, TradingJournal

console = Console()

_ALLOWED_CATEGORIES = {
    "DATA_ERROR", "SIGNAL_ERROR", "TIMING_ERROR", "MARKET_EVENT", "MARKET_REGIME",
    "STOP_TOO_TIGHT", "SECTOR_HEADWIND", "FUNDAMENTAL_DETERIORATION",
    "SENTIMENT_REVERSAL", "OVERCONFIDENCE", "COSTS_ATE_PROFIT", "UNKNOWN",
}

# Losses we should NOT learn from — they reflect market noise, not a bad signal.
NOISE_CATEGORIES = {"MARKET_EVENT", "SECTOR_HEADWIND", "MARKET_REGIME"}

# Losses that ARE genuine signal failures — these update the knowledge base.
SIGNAL_CATEGORIES = {
    "SIGNAL_ERROR", "DATA_ERROR", "TIMING_ERROR", "STOP_TOO_TIGHT",
    "OVERCONFIDENCE", "FUNDAMENTAL_DETERIORATION", "COSTS_ATE_PROFIT",
}

# failure_category → KnowledgeEntry.category
_KNOWLEDGE_CATEGORY = {
    "MARKET_REGIME": "MARKET_REGIME_PATTERN",
    "MARKET_EVENT": "MARKET_REGIME_PATTERN",
    "SECTOR_HEADWIND": "SECTOR_PATTERN",
    "SIGNAL_ERROR": "INDICATOR_PATTERN",
    "FUNDAMENTAL_DETERIORATION": "FUNDAMENTAL_PATTERN",
    "SENTIMENT_REVERSAL": "FUNDAMENTAL_PATTERN",
    "TIMING_ERROR": "TIMING_PATTERN",
    "STOP_TOO_TIGHT": "INDICATOR_PATTERN",
    "COSTS_ATE_PROFIT": "INDICATOR_PATTERN",
}

_SYSTEM = (
    "You are a post-trade forensic analyst. Your only job is to identify exactly why "
    "this trade lost money and what signal was missed. Be specific. Generic answers "
    "like 'market moved against us' are not acceptable. Find the root cause."
)


class RootCauseAnalyzer:
    def __init__(self, journal: TradingJournal | None = None) -> None:
        self.journal = journal or TradingJournal()

    # ── heuristic pre-classification (no LLM) ──────────────────────────────────
    def _preclassify(self, trade: TradeRecord, snapshot: dict) -> str:
        market = snapshot.get("market_context", {}) or {}
        judge = snapshot.get("judge_verdict", {}) or {}
        flags = judge.get("flags", []) or []

        pnl_pct = trade.pnl_pct
        gross_pnl_pct = getattr(trade, "gross_pnl_pct", None)
        if gross_pnl_pct is not None and gross_pnl_pct > 0 and pnl_pct is not None and pnl_pct < 0:
            return "COSTS_ATE_PROFIT"
        if pnl_pct is not None and pnl_pct < -(LIMITS.stop_loss_pct + 1):
            return "STOP_TOO_TIGHT"
        if market.get("nifty_trend") == "DOWNTREND":
            return "MARKET_REGIME"
        if "FUNDAMENTAL_WEAK" in flags or "FUNDAMENTAL_DETERIORATING" in flags:
            return "FUNDAMENTAL_DETERIORATION"
        if "CHOPPY_MARKET" in flags:
            return "SIGNAL_ERROR"
        if snapshot.get("manually_requested"):
            return "OVERCONFIDENCE"
        return "UNKNOWN"

    def _default_pattern(self, category: str, sector: str) -> dict:
        if category == "COSTS_ATE_PROFIT":
            return {
                "pattern_id": "costs-ate-profit-general",
                "description": "Tight-target trades don't clear Indian costs — widen targets",
                "category": _KNOWLEDGE_CATEGORY[category],
            }
        sector_slug = (sector or "general").lower().replace(" ", "-")
        return {
            "pattern_id": f"{category.lower().replace('_', '-')}-{sector_slug}",
            "description": (
                f"{sector or 'General'} trades failing via {category.replace('_', ' ').lower()}"
            ),
            "category": _KNOWLEDGE_CATEGORY.get(category, "MARKET_REGIME_PATTERN"),
        }

    # ── main ───────────────────────────────────────────────────────────────────
    def analyze(self, trade: TradeRecord, state_snapshot: dict) -> RootCauseRecord:
        state_snapshot = state_snapshot or {}
        sector = state_snapshot.get("sector", "") or (trade.sector or "")
        category = self._preclassify(trade, state_snapshot)

        # Only an UNKNOWN loss benefits from the market-event check (the network call).
        # MARKET_REGIME is already labeled noise, so reclassifying it changes nothing.
        if category == "UNKNOWN" and self._detect_market_event(trade, state_snapshot):
            category = "MARKET_EVENT"

        fundamental = state_snapshot.get("fundamental_verdict", {}) or {}
        technical = state_snapshot.get("technical_verdict", {}) or {}
        judge = state_snapshot.get("judge_verdict", {}) or {}
        indicators = technical.get("indicators", {}) or {}

        rca_text = (
            f"Pre-classified as {category}. Entry on signal={technical.get('signal')} "
            f"with fundamental score {fundamental.get('score')} and judge flags "
            f"{judge.get('flags', [])}. Closed at pnl_pct={trade.pnl_pct}."
        )
        what_missed = "Heuristic only — no LLM available to pinpoint the missed signal."
        lesson = f"In future: when category={category} conditions recur, size down or skip."
        pattern = self._default_pattern(category, sector)

        # Optional LLM deep dive (Haiku).
        if settings.has_anthropic_key:
            prompt = (
                f"Symbol: {trade.symbol}, sector: {sector}\n"
                f"Entry thesis: fundamental score={fundamental.get('score')}, "
                f"strengths={fundamental.get('strengths')}, technical signal={technical.get('signal')}\n"
                f"Indicators at entry: RSI={indicators.get('rsi_14')} ADX={indicators.get('adx_signal')} "
                f"MACD={indicators.get('macd')} trend={indicators.get('trend')}\n"
                f"Judge reasoning: {judge.get('reasoning')} flags={judge.get('flags')}\n"
                f"Outcome: pnl_pct={trade.pnl_pct}, market at entry="
                f"{state_snapshot.get('market_context', {}).get('context_summary')}\n"
                f"Pre-classified category: {category}\n\n"
                "Respond ONLY with JSON:\n"
                "{\n"
                f'  "failure_category": <one of {sorted(_ALLOWED_CATEGORIES)}>,\n'
                '  "root_cause_analysis": "<100-150 words, specific>",\n'
                '  "what_signal_missed": "<one indicator/signal that would have helped>",\n'
                '  "actionable_lesson": "<In future: when [X], [Y]. Never [Z].>",\n'
                '  "knowledge_pattern": {"pattern_id": "<slug>", "description": "<str>",\n'
                '     "category": "SECTOR_PATTERN|INDICATOR_PATTERN|TIMING_PATTERN|'
                'FUNDAMENTAL_PATTERN|MARKET_REGIME_PATTERN"}\n'
                "}"
            )
            try:
                resp = get_llm(temperature=0).invoke([("system", _SYSTEM), ("human", prompt)])
                parsed = parse_json_response(getattr(resp, "content", "") or "")
            except Exception as exc:
                console.print(f"[yellow]RCA LLM call failed: {exc}[/yellow]")
                parsed = {}

            if parsed:
                cat = parsed.get("failure_category")
                if cat in _ALLOWED_CATEGORIES:
                    category = cat
                rca_text = parsed.get("root_cause_analysis", rca_text)
                what_missed = parsed.get("what_signal_missed", what_missed)
                lesson = parsed.get("actionable_lesson", lesson)
                kp = parsed.get("knowledge_pattern") or {}
                if kp.get("pattern_id") and kp.get("description"):
                    pattern = {
                        "pattern_id": kp["pattern_id"],
                        "description": kp["description"],
                        "category": kp.get("category", pattern["category"]),
                    }

        record = RootCauseRecord(
            trade_id=trade.id,
            symbol=trade.symbol,
            failure_category=category,
            root_cause_analysis=rca_text,
            what_signal_missed=what_missed,
            actionable_lesson=lesson,
        )
        self.journal.log_rca(record)

        # Noise-aware learning: only genuine signal failures update the knowledge base.
        # MARKET_EVENT / SECTOR_HEADWIND losses are recorded for audit but DON'T
        # corrupt patterns — noise doesn't mean our signal was wrong.
        if category in SIGNAL_CATEGORIES:
            self._upsert_pattern(pattern, trade)
        else:
            console.print(
                f"[dim]NOISE LOSS — skipping knowledge update. Category: {category}[/dim]"
            )
        return record

    def _detect_market_event(self, trade: TradeRecord, state_snapshot: dict) -> bool:
        """True if a broad market move likely swamped the individual stock (noise)."""
        try:
            close_date = trade.closed_at
            if close_date is None:
                return False
            from src.data.fetcher import MarketDataFetcher

            nifty_df = MarketDataFetcher().get_price_history("^NSEI", period="5d")
            if nifty_df is not None and len(nifty_df) > 0:
                daily_ret = nifty_df["Close"].pct_change().abs()
                for d, ret in daily_ret.items():
                    if abs((d.date() - close_date.date()).days) <= 1 and ret > 0.02:
                        return True
        except Exception:
            pass
        return False

    def _upsert_pattern(self, pattern: dict, trade: TradeRecord) -> None:
        existing = [
            e for e in self.journal.get_active_knowledge(min_confidence=0.0)
            if e.pattern_id == pattern["pattern_id"]
        ]
        if existing:
            self.journal.update_knowledge_confidence(pattern["pattern_id"], confirmed=True)
        else:
            self.journal.log_knowledge_entry(
                KnowledgeEntry(
                    pattern_id=pattern["pattern_id"],
                    pattern_description=pattern["description"],
                    category=pattern["category"],
                    confidence=0.1,
                    observed_count=1,
                    first_seen=datetime.now(),
                    last_confirmed=datetime.now(),
                    last_seen_in_trade=trade.symbol,
                    is_hypothesis=True,
                    supporting_trades=json.dumps([trade.id]),
                )
            )

    # ── wins confirm standing patterns ─────────────────────────────────────────
    def analyze_win(self, trade: TradeRecord, state_snapshot: dict) -> None:
        state_snapshot = state_snapshot or {}
        for entry in self.journal.get_active_knowledge(min_confidence=0.0):
            if self._pattern_applies(entry, state_snapshot):
                self.journal.update_knowledge_confidence(entry.pattern_id, confirmed=True)

    def _pattern_applies(self, entry: KnowledgeEntry, state: dict) -> bool:
        desc = (entry.pattern_description or "").lower()
        technical = state.get("technical_verdict", {}) or {}
        indicators = technical.get("indicators", {}) or {}

        if entry.category == "SECTOR_PATTERN":
            sector = (state.get("sector") or "").lower()
            return bool(sector and sector in desc)

        if entry.category == "INDICATOR_PATTERN":
            for kw in ("rsi", "adx", "macd", "obv", "volume", "stoch"):
                if kw in desc and indicators:
                    return True
            return False

        if entry.category == "TIMING_PATTERN":
            now = datetime.now()
            tokens = (now.strftime("%A").lower(), now.strftime("%B").lower())
            return any(t in desc for t in tokens)

        if entry.category == "MARKET_REGIME_PATTERN":
            trend = (state.get("market_context", {}) or {}).get("nifty_trend", "").lower()
            return bool(trend and trend in desc)

        return False
