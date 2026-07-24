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
from src.llm import cached_llm_call, parse_json_response
from src.memory.lessons import LessonsRetriever
from src.orchestrator.state import TradeState
from src.trading.modes import get_current_mode

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
    "ML_JUDGE_DISAGREEMENT", "COSTS_EAT_EDGE",
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

        mode = get_current_mode()
        if fundamental.get("hard_rejected"):
            flags.append("HARD_REJECTED_FUNDAMENTAL")
        if technical.get("proceed") is False and signal == "SKIP":
            flags.append("TECHNICAL_SKIP")
        if rr is not None and rr < 1.5:
            flags.append("POOR_RISK_REWARD")
        # Only auto-veto on choppy ADX in modes that require a trending market
        # (conserve). Balanced/rogue let the LLM scorecard weigh chop instead
        # of a hard pre-LLM rejection — this was previously unconditional,
        # silently overriding balanced/rogue mode's intent.
        if adx_signal == "CHOPPY" and mode.require_adx_trending:
            flags.append("CHOPPY_MARKET")
        # Rogue mode intentionally trades downtrends — it lowers the bar but never
        # bypasses the rest of the pipeline (stop-loss + judge scorecard still apply).
        if not mode.trades_downtrends:
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

        # Costs veto — a trade whose expected move can't clear 3x round-trip costs
        # is a fiction of an edge once STT/brokerage/GST/slippage are applied.
        if entry and target:
            try:
                from src.data.watchlist import ALL_STOCKS
                from src.trading.cost_model import compute_round_trip_costs

                symbol = state.get("symbol", "")
                tier = ALL_STOCKS.get(symbol, {}).get("tier", "large")
                expected_move_pct = (target - entry) / entry * 100
                est_cost_pct = compute_round_trip_costs(entry, target, 1, tier).total_pct
                if expected_move_pct < est_cost_pct * 3:
                    veto.append("COSTS_EAT_EDGE")
                    console.print(
                        f"[red][JUDGE] Expected move {expected_move_pct:.2f}% doesn't clear "
                        f"3x costs {est_cost_pct * 3:.2f}%[/red]")
            except Exception:
                pass

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

        # The trading mode sets the risk dial's home base; the adaptive threshold
        # (learned from forced/simulated trade outcomes by regime) nudges it within
        # a bounded band so evidence can shift the bar without overriding the mode.
        # Regime comes from RegimeDetector, NOT market_context (which uses a
        # different UPTREND/DOWNTREND/SIDEWAYS vocabulary) — RegimeDetector's
        # BULL_TRENDING/VOLATILE/etc. is the same taxonomy AlwaysOnTrader tags
        # forced trades with, so the two sides of the loop actually match up.
        mode = get_current_mode()
        from src.data.regime_detector import RegimeDetector
        from src.memory.adaptive_thresholds import AdaptiveThresholds

        try:
            regime = RegimeDetector().detect().get("regime", "UNKNOWN")
        except Exception:
            regime = "UNKNOWN"
        adaptive_threshold = AdaptiveThresholds().get_judge_threshold(regime)
        threshold = round(max(
            mode.judge_threshold - 1.5,
            min(mode.judge_threshold + 1.5, adaptive_threshold)), 2)

        if settings.effective_demo_mode:
            fund = fundamental.get("score", 0) or 0
            tech = technical.get("score", 0) or 0
            overall = round((fund * 0.5 + tech * 0.5) * 10, 2)
            flags = ["DEMO_MODE"]
            if manually_requested:
                flags.append("MANUALLY_REQUESTED")
            return {
                "approved": overall >= threshold,
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
            verdict = parse_json_response(
                cached_llm_call(system, prompt, model=settings.llm_model_judge))
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

        # Blend the XGBoost win-probability (once trained on 30+ real trades).
        try:
            from src.ml.signal_combiner import SignalCombiner

            ml_prob = SignalCombiner().predict_win_probability(state)
        except Exception:
            ml_prob = None

        # Random Forest (trained on simulation data — activates far earlier
        # than XGBoost, which needs 30+ REAL trades that don't exist yet).
        try:
            from src.ml.random_forest_model import RandomForestModel

            from src.data.watchlist import ALL_STOCKS

            rf_indicators = technical.get("indicators", {}) or {}
            rf_meta = {"tier": ALL_STOCKS.get(state.get("symbol", ""), {}).get("tier", "large"),
                      "signal_score": technical.get("score", 0.5)}
            rf_prob = RandomForestModel().predict_win_probability(rf_indicators, rf_meta)
        except Exception:
            rf_prob = None

        combined_ml = None
        if rf_prob is not None and ml_prob is not None:
            combined_ml = 0.5 * rf_prob + 0.5 * ml_prob
        elif rf_prob is not None:
            combined_ml = rf_prob
        elif ml_prob is not None:
            combined_ml = ml_prob

        if combined_ml is not None:
            blended = 0.55 * (overall / 10) + 0.45 * combined_ml
            if abs((overall / 10) - combined_ml) > 0.3:
                flags.append("ML_JUDGE_DISAGREEMENT")
            overall = round(blended * 10, 2)
            reasoning_bits = verdict.get("reasoning", "")
            if ml_prob is not None:
                reasoning_bits += f" | XGBoost: {ml_prob:.0%} win probability"
            if rf_prob is not None:
                reasoning_bits += f" | RF model: {rf_prob:.0%} win probability"
            verdict["reasoning"] = reasoning_bits

        verdict["flags"] = flags
        verdict["overall_score"] = round(overall, 4)
        verdict["score"] = round(overall / 10, 4)  # 0-1 for journal/back-compat
        verdict["approved"] = bool(overall >= threshold)
        return verdict
