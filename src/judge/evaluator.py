"""
LLM-as-judge — a senior risk officer that protects capital.

Cheap deterministic auto-vetoes run BEFORE the LLM (saving tokens): hard-rejected
fundamentals, technical SKIP, poor R:R, choppy market, or fighting the Nifty/weekly
trend. Survivors get a weighted multi-criteria scorecard; approval needs >= 7.5/10.
Works in paper mode with no API key (mock approval unless an auto-veto fires).
"""
from __future__ import annotations

from rich.console import Console

from config.settings import settings
from src.data.market_context import MarketContext
from src.llm import get_judge_llm, parse_json_response
from src.memory.lessons import LessonsRetriever
from src.orchestrator.state import TradeState
from src.utils.adaptive import get_adaptive_params

console = Console()

_SYSTEM = (
    "You are a senior risk officer at a prop trading desk. Your job is to protect "
    "capital. You are paid to find reasons NOT to trade. Approve only when all "
    "criteria align. Be skeptical of confidence > 0.9 — markets are uncertain."
)

_VALID_FLAGS = {
    "EMOTIONAL_TRADE_SUSPECTED", "LOW_CONVICTION", "POOR_RISK_REWARD",
    "FUNDAMENTAL_WEAK", "TECHNICAL_CONFLICT", "MANUALLY_REQUESTED", "NO_API_KEY",
    "CHOPPY_MARKET", "WEEKLY_TREND_CONFLICT", "FUNDAMENTAL_DETERIORATING",
    "PROMOTER_PLEDGE_RISK", "EARNINGS_PROXIMITY", "FIGHTING_NIFTY",
    "HARD_REJECTED_FUNDAMENTAL", "TECHNICAL_SKIP",
    "SUPERTREND_BEARISH", "SELLING_PRESSURE", "BELOW_ALL_SUPPORTS",
}

APPROVAL_THRESHOLD = 7.5  # out of 10


class LLMJudge:
    def __init__(self) -> None:
        self.market = MarketContext()
        self.lessons = LessonsRetriever()

    def _auto_veto(self, fundamental, technical, market_context, rr) -> list[str]:
        flags: list[str] = []
        signal = technical.get("signal")
        indicators = technical.get("indicators", {})
        adx_signal = technical.get("adx_signal") or indicators.get("adx_signal")
        nifty_trend = market_context.get("nifty_trend")
        weekly_trend = technical.get("weekly_trend") or indicators.get("weekly_trend")

        if fundamental.get("hard_rejected"):
            flags.append("HARD_REJECTED_FUNDAMENTAL")
        if technical.get("proceed") is False and signal == "SKIP":
            flags.append("TECHNICAL_SKIP")
        if rr is not None and rr < 1.5:
            flags.append("POOR_RISK_REWARD")
        if adx_signal == "CHOPPY":
            flags.append("CHOPPY_MARKET")
        if nifty_trend == "DOWNTREND" and signal == "BUY":
            flags.append("FIGHTING_NIFTY")
        if weekly_trend == "DOWNTREND" and signal == "BUY":
            flags.append("WEEKLY_TREND_CONFLICT")

        # Phase 4 — supertrend / money-flow / support auto-vetoes.
        cmf = indicators.get("cmf_20")
        if indicators.get("supertrend_direction") == "BEARISH" and signal == "BUY":
            flags.append("SUPERTREND_BEARISH")
        if cmf is not None and cmf < -0.15 and signal == "BUY":
            flags.append("SELLING_PRESSURE")
        if indicators.get("price_vs_vwap") == "BELOW" and indicators.get("nearest_pivot_level") == "BELOW_S1":
            flags.append("BELOW_ALL_SUPPORTS")
        return flags

    def evaluate(self, state: TradeState) -> dict:
        fundamental = state.get("fundamental_verdict", {})
        technical = state.get("technical_verdict", {})
        market_context = state.get("market_context") or self.market.get_nifty_context()
        manually_requested = bool(state.get("manually_requested"))

        entry = technical.get("entry_price") or 0
        stop = technical.get("stop_price") or 0
        target = technical.get("target_price") or 0
        rr = round((target - entry) / (entry - stop), 4) if (entry - stop) else None

        # ── Auto-veto (pre-LLM, runs even with no API key) ──
        veto = self._auto_veto(fundamental, technical, market_context, rr)
        if veto:
            if manually_requested:
                veto.append("MANUALLY_REQUESTED")
            reason = "Auto-veto: " + ", ".join(veto)
            console.print(f"[red][JUDGE] {reason}[/red]")
            return {
                "approved": False,
                "score": 0.0,
                "overall_score": 0.0,
                "dimension_scores": {},
                "reasoning": reason,
                "one_line_verdict": "Vetoed before scorecard.",
                "flags": veto,
            }

        threshold = get_adaptive_params().get("judge_approval_threshold", APPROVAL_THRESHOLD)

        if settings.effective_demo_mode:
            fund = fundamental.get("score", 0) or 0
            tech = technical.get("score", 0) or 0
            overall = round((fund * 0.5 + tech * 0.5) * 10, 2)
            flags = ["DEMO_MODE"]
            if manually_requested:
                flags.append("MANUALLY_REQUESTED")
            return {
                "approved": overall >= 6.0,
                "score": round(overall / 10, 4),
                "overall_score": overall,
                "dimension_scores": {},
                "reasoning": f"Rule-based {fund:.2f}/{tech:.2f}",
                "one_line_verdict": "DEMO",
                "flags": flags,
            }

        # Context blocks A (knowledge) + B (lessons) + C (recent RCAs).
        sector = state.get("sector")
        symbol = state.get("symbol", "")
        knowledge_context = state.get("knowledge_context") or ""
        lessons = state.get("lessons") or self.lessons.get_relevant_lessons(symbol, sector)
        rca_context = self.lessons.get_recent_rca_context(symbol, sector)
        system = _SYSTEM
        blocks = []
        try:
            from src.analytics.performance import PerformanceAnalyzer

            traj = PerformanceAnalyzer(self.lessons.journal).get_performance_trajectory()
            blocks.append(f"SYSTEM PERFORMANCE: {traj['context_sentence']}")
        except Exception:
            pass
        if knowledge_context:
            blocks.append(knowledge_context)
        if lessons:
            blocks.append(f"What the system has learned:\n{lessons}")
        if rca_context:
            blocks.append(rca_context)
        if blocks:
            system = _SYSTEM + "\n\n" + "\n\n".join(blocks)

        sentiment = state.get("sentiment", {}) or fundamental.get("sentiment", {}) or {}

        prompt = (
            f"Fundamental: score={fundamental.get('score')} trend={fundamental.get('trend')} "
            f"pe_vs_sector={fundamental.get('pe_vs_sector')} moat_strength={fundamental.get('moat_strength')}\n"
            f"  ROCE/promoter from data: {fundamental.get('data', {})}\n"
            f"Technical: score={technical.get('score')} signal={technical.get('signal')} "
            f"weekly_trend={technical.get('weekly_trend')} adx={technical.get('indicators', {}).get('adx_signal')} "
            f"pattern={technical.get('indicators', {}).get('candlestick_pattern')}\n"
            f"Social sentiment: {sentiment.get('sentiment_label', 'N/A')} "
            f"(score {sentiment.get('sentiment_score', 0)}); red_flags={sentiment.get('red_flags', [])}\n"
            f"Levels: Entry={entry} Stop={stop} Target={target} | R:R={rr}\n"
            f"Market: {market_context.get('context_summary')}\n"
            f"Manually requested by user: {manually_requested}\n\n"
            "Score each dimension 0-10, then a weighted overall (approve only if overall >= 7.5):\n"
            "  fundamental_quality (30%): ROCE, moat, 3yr trend, promoter data\n"
            "  technical_setup (25%): pattern, indicators, ADX, weekly alignment\n"
            "  risk_reward (20%): the explicit R:R\n"
            "  market_context (15%): Nifty + sector trend support\n"
            "  conviction_consistency (10%): do fundamental and technical agree?\n\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "approved": <bool>,\n'
            '  "overall_score": <float 0-10>,\n'
            '  "dimension_scores": {"fundamental_quality": <0-10>, "technical_setup": <0-10>,\n'
            '     "risk_reward": <0-10>, "market_context": <0-10>, "conviction_consistency": <0-10>},\n'
            f'  "flags": [<subset of {sorted(_VALID_FLAGS)}>],\n'
            '  "reasoning": "<concise>",\n'
            '  "one_line_verdict": "<one line>"\n'
            "}"
        )

        try:
            resp = get_judge_llm(temperature=0).invoke([("system", system), ("human", prompt)])
            verdict = parse_json_response(getattr(resp, "content", "") or "")
        except Exception as exc:
            console.print(f"[yellow]Judge LLM call failed: {exc}[/yellow]")
            verdict = {}

        if not verdict:
            return {
                "approved": False, "score": 0.0, "overall_score": 0.0,
                "dimension_scores": {}, "reasoning": "Judge response unparseable; vetoing for safety.",
                "one_line_verdict": "Unparseable — vetoed.", "flags": ["LOW_CONVICTION"],
            }

        overall = float(verdict.get("overall_score", 0) or 0)
        flags = [f for f in verdict.get("flags", []) if f in _VALID_FLAGS]
        if manually_requested and "MANUALLY_REQUESTED" not in flags:
            flags.append("MANUALLY_REQUESTED")
        verdict["flags"] = flags
        verdict["overall_score"] = round(overall, 4)
        verdict["score"] = round(overall / 10, 4)  # 0-1 for journal/back-compat
        verdict["approved"] = bool(overall >= threshold)
        return verdict
