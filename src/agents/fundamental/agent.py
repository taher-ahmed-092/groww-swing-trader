"""
Fundamental agent.

Code gathers and merges the numbers (screener.in primary, yfinance fallback),
runs deterministic hard-reject and trend/peer checks, then hands the LLM ONLY the
computed values to interpret qualitatively (CLAUDE.md rule 4). The LLM never sees
raw dicts and never computes ratios.
"""
from __future__ import annotations

from rich.console import Console

from config.settings import settings
from src.agents.fundamental.ratios import compute_ratios
from src.agents.sentiment.agent import SocialSentimentAgent
from src.data.fetcher import MarketDataFetcher
from src.data.screener import ScreenerScraper
from src.llm import get_llm, parse_json_response
from src.memory.lessons import LessonsRetriever
from src.orchestrator.state import TradeState

console = Console()

# Red-flag keywords that warrant a fundamental penalty when seen in social sentiment.
_SENTIMENT_REGULATORY = ("fraud", "scam", "sebi", "ed raid", "regulatory", "default",
                         "insolvency", "npa")

# Rough sector P/E benchmarks for a cheap/fair/expensive read.
_SECTOR_PE_BENCHMARK = {
    "Banking": 15, "IT": 28, "FMCG": 40, "Pharma": 30,
    "Auto": 20, "Energy": 12, "Metals": 10, "Infra": 18,
}

_SYSTEM = (
    "You are an equity fundamental analyst. You are given pre-computed financial "
    "ratios and derived classifications for a single company. Interpret them "
    "qualitatively. You MUST NOT invent or recompute any numbers — reason only from "
    "the values provided."
)


class FundamentalAgent:
    def __init__(self) -> None:
        self.fetcher = MarketDataFetcher()
        self.screener = ScreenerScraper()
        self.lessons = LessonsRetriever()
        self.sentiment_agent = SocialSentimentAgent()

    def _apply_sentiment(self, verdict: dict, sentiment: dict) -> dict:
        """Surface social red flags into the verdict; penalize on multiple flags."""
        verdict["sentiment"] = sentiment
        red_flags = sentiment.get("red_flags", []) or []
        regulatory = [f for f in red_flags if any(k in str(f).lower() for k in _SENTIMENT_REGULATORY)]
        if regulatory:
            weaknesses = list(verdict.get("weaknesses", []))
            for f in regulatory:
                weaknesses.append(f"(RED FLAG — SOCIAL) {f}")
            verdict["weaknesses"] = weaknesses
        if len(red_flags) >= 2 and verdict.get("score") is not None:
            verdict["score"] = round(max(0.0, verdict["score"] - 0.1), 4)
        return verdict

    def _gather(self, symbol: str) -> dict:
        """Merge screener (primary) over yfinance-derived ratios (fallback)."""
        sdata = self.screener.get_company_data(symbol) or {}
        yf_ratios = compute_ratios(self.fetcher.get_fundamentals(symbol) or {})

        def pick(screener_key: str, yf_key: str):
            """Screener value wins; fall back to the yfinance-derived ratio."""
            value = sdata.get(screener_key)
            return value if value is not None else yf_ratios.get(yf_key)

        # yfinance reports debtToEquity as a PERCENT (e.g. 9.8 == D/E 0.098), while
        # screener.in and our hard-reject threshold (>3.0) use the RATIO form.
        # Normalize the yfinance fallback so a low-debt name isn't falsely rejected.
        dte = sdata.get("debt_to_equity")
        if dte is None:
            yf_dte = yf_ratios.get("debt_to_equity")
            dte = round(yf_dte / 100, 4) if yf_dte is not None else None

        return {
            "name": sdata.get("name"),
            "sector": sdata.get("sector"),
            "market_cap_cr": sdata.get("market_cap_cr"),
            "pe_ratio": pick("pe_ratio", "pe_ratio"),
            "pb_ratio": pick("pb_ratio", "pb_ratio"),
            "div_yield": pick("div_yield", "dividend_yield"),
            "roce_pct": sdata.get("roce_pct"),
            "roe_pct": pick("roe_pct", "roe"),
            "debt_to_equity": dte,
            "current_ratio": pick("current_ratio", "current_ratio"),
            "promoter_holding_pct": sdata.get("promoter_holding_pct"),
            "promoter_pledged_pct": sdata.get("promoter_pledged_pct"),
            "sales_growth_3yr": sdata.get("sales_growth_3yr"),
            "profit_growth_3yr": sdata.get("profit_growth_3yr"),
            "revenue_ttm_cr": sdata.get("revenue_ttm_cr"),
            "net_profit_ttm_cr": sdata.get("net_profit_ttm_cr"),
        }

    def _trend(self, data: dict) -> str:
        sales, profit = data.get("sales_growth_3yr"), data.get("profit_growth_3yr")
        if sales is None or profit is None:
            return "UNKNOWN"
        if sales > 10 and profit > 10:
            return "IMPROVING"
        if sales < 0 and profit < 0:
            return "DECLINING"
        return "MIXED"

    def _pe_vs_sector(self, data: dict, sector: str | None) -> str:
        pe = data.get("pe_ratio")
        benchmark = _SECTOR_PE_BENCHMARK.get(sector or "")
        if pe is None or not benchmark:
            return "UNKNOWN"
        if pe < benchmark * 0.8:
            return "CHEAP"
        if pe > benchmark * 1.2:
            return "EXPENSIVE"
        return "FAIR"

    def analyze(self, state: TradeState) -> dict:
        symbol = state["symbol"]
        sector = state.get("sector") or None

        data = self._gather(symbol)
        if not sector:
            sector = data.get("sector")

        # ── Hard-reject gate (deterministic, pre-LLM — saves tokens) ──
        rejects = self.screener.check_hard_rejects(data)
        if rejects:
            console.print(f"[red][FUNDAMENTAL] {symbol} AUTO-REJECTED: {'; '.join(rejects)}[/red]")
            return {
                "score": 0.0,
                "proceed": False,
                "hard_rejected": True,
                "reasoning": "AUTO-REJECTED: " + "; ".join(rejects),
                "strengths": [],
                "weaknesses": rejects,
                "swot": {},
                "moat": "n/a",
                "data": data,
            }

        trend = self._trend(data)
        pe_vs_sector = self._pe_vs_sector(data, sector)

        # Social sentiment sub-agent (never raises; neutral on failure).
        company_name = data.get("name") or symbol
        sentiment = self.sentiment_agent.analyze(symbol, company_name)

        base_extra = {
            "hard_rejected": False,
            "trend": trend,
            "pe_vs_sector": pe_vs_sector,
            "data": data,
        }

        if not settings.has_anthropic_key:
            verdict = {
                "score": 0.5, "strengths": [], "weaknesses": [], "swot": {},
                "moat": "unknown", "moat_strength": "UNKNOWN", "proceed": True,
                "reasoning": "MOCK — no ANTHROPIC_API_KEY; numbers computed but not interpreted.",
                **base_extra,
            }
            return self._apply_sentiment(verdict, sentiment)

        # Context blocks injected at the TOP of the prompt (before stock-specific data).
        knowledge_context = state.get("knowledge_context") or ""
        lessons = state.get("lessons") or self.lessons.get_relevant_lessons(symbol, sector)
        system = _SYSTEM
        context_blocks = []
        if knowledge_context:
            context_blocks.append(knowledge_context)
        if lessons:
            context_blocks.append(f"What the system has learned:\n{lessons}")
        if context_blocks:
            system = _SYSTEM + "\n\n" + "\n\n".join(context_blocks)

        prompt = (
            f"Company: {symbol} ({data.get('name')}), sector: {sector}\n"
            f"Computed numbers (authoritative, do not change):\n{data}\n"
            f"Derived: 3yr growth trend = {trend}; valuation vs sector = {pe_vs_sector}\n"
            f"SOCIAL SENTIMENT: {sentiment.get('sentiment_label')} "
            f"(score: {sentiment.get('sentiment_score'):.2f})\n"
            f"Key events: {sentiment.get('key_events')}\n"
            f"Red flags: {sentiment.get('red_flags') or 'None'}\n"
            f"Corporate actions: {sentiment.get('corporate_actions') or 'None'}\n\n"
            "Pay explicit attention to ROCE (capital efficiency) and promoter pledging "
            "(a manipulation/risk flag). Then answer.\n\n"
            "Respond ONLY with JSON of the form:\n"
            "{\n"
            '  "score": <float 0-1, fundamental quality>,\n'
            '  "strengths": [<5 specific strengths>],\n'
            '  "weaknesses": [<5 specific weaknesses>],\n'
            '  "swot": {"strengths": [], "weaknesses": [], "opportunities": [], "threats": []},\n'
            '  "moat": "<one-line moat assessment>",\n'
            '  "moat_strength": "NONE" | "WEAK" | "MODERATE" | "STRONG",\n'
            '  "buffett_3yr_hold": <bool, "would Warren Buffett hold this for 3+ years?">,\n'
            '  "proceed": <bool>,\n'
            '  "reasoning": "<concise rationale>"\n'
            "}"
        )

        try:
            resp = get_llm(temperature=0).invoke([("system", system), ("human", prompt)])
            verdict = parse_json_response(getattr(resp, "content", "") or "")
        except Exception as exc:
            console.print(f"[yellow]Fundamental LLM call failed: {exc}[/yellow]")
            verdict = {}

        if not verdict:
            verdict = {
                "score": 0.5, "strengths": [], "weaknesses": [], "swot": {},
                "moat": "unknown", "moat_strength": "UNKNOWN", "proceed": False,
                "reasoning": "LLM response unparseable; defaulting to no-proceed.",
            }
        verdict.update(base_extra)
        return self._apply_sentiment(verdict, sentiment)
