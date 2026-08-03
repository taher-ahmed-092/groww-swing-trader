"""
Technical agent.

Code computes the indicators (indicators.py) AND the deterministic levels: an
ATR-based, volatility-aware stop (falling back to a % stop), target from the reward
ratio. It also derives the weekly trend and never lets a BUY fight the weekly trend.
The LLM only interprets values and emits a signal (CLAUDE.md rule 4).
"""
from __future__ import annotations

from rich.console import Console

from config.risk_limits import LIMITS
from config.settings import settings
from src.agents.technical.entry_price import recommend_entry
from src.agents.technical.indicators import compute_indicators
from src.agents.technical.rule_score import rule_based_technical_score
from src.data.fetcher import MarketDataFetcher
from src.data.market_context import MarketContext
from src.llm import cached_llm_call, parse_json_response
from src.memory.lessons import LessonsRetriever
from src.orchestrator.state import TradeState
from src.strategies.registry import StrategyRegistry
from src.tracking.signal_tracker import SignalTracker
from src.trading.time_window import TradingTimeWindow

console = Console()

_SYSTEM = (
    "You are a technical analyst. You are given pre-computed indicator values, "
    "fixed entry/stop/target levels, the weekly trend, and broad market context. "
    "Interpret the setup and emit a signal. You MUST NOT invent or recompute any "
    "numbers — reason only from the values provided."
)


class TechnicalAgent:
    def __init__(self) -> None:
        self.fetcher = MarketDataFetcher()
        self.market = MarketContext()
        self.lessons = LessonsRetriever()

    def _levels(self, entry_price: float, indicators: dict, tier: str = "large") -> tuple[float, float, list[str]]:
        # Prefer the ATR-based stop; fall back to the % stop if ATR is unavailable
        # or produces a nonsensical (non-positive / above-entry) level.
        atr_stop = indicators.get("atr_stop")
        if atr_stop is not None and 0 < atr_stop < entry_price:
            stop_price = round(float(atr_stop), 4)
        else:
            stop_price = round(entry_price * (1 - LIMITS.stop_loss_pct / 100), 4)

        # Cost-aware target floor (Fix — was previously a FIXED reward ratio
        # (LIMITS.target_reward_ratio) applied to the ATR stop distance,
        # neither resistance-based nor cost-aware: a tight ATR stop times a
        # flat 2:1 ratio can produce a target whose net move barely clears
        # round-trip costs (same failure mode already fixed in
        # always_on_trader._stop_target_for_costs — mirrored here). ratio
        # is the higher of the configured floor and 8x the round-trip cost
        # as a fraction of the stop distance, so the target always clears
        # costs with margin regardless of how tight the ATR stop is.
        target_flags: list[str] = []
        stop_distance_pct = (entry_price - stop_price) / entry_price * 100 if entry_price else 0.0
        ratio = LIMITS.target_reward_ratio
        if stop_distance_pct > 0:
            try:
                from src.trading.cost_model import compute_round_trip_costs

                round_trip_cost_pct = compute_round_trip_costs(
                    entry_price, entry_price * 1.06, 1, tier).total_pct
                ratio = max(LIMITS.target_reward_ratio, 8 * round_trip_cost_pct / stop_distance_pct)
            except Exception:
                pass
        cost_aware_target = round(entry_price + (entry_price - stop_price) * ratio, 4)

        # If realistic resistance (nearest pivot R1) sits below the cost-aware
        # target, don't force the target past it — flag it and let the target
        # sit at the more realistic level; the risk checker's net R:R gate
        # rejects it normally if that's now too tight to clear costs, rather
        # than this function fabricating an unreachable target.
        #
        # BUT: R1 = 2*pivot - prior_low sits 0.5-1% above entry for a stock
        # near its recent high, which is noise relative to a ~7% stop
        # (gross R:R ~0.1:1) — every candidate hit POOR_RISK_REWARD as a
        # result. Only trust R1 as a real cap when it's at least
        # min_resistance_distance_ratio (config) multiples of the stop
        # distance above entry; otherwise it's too close to be a meaningful
        # target and the cost-aware target is used instead.
        resistance = indicators.get("r1")
        target_price = cost_aware_target
        stop_distance = entry_price - stop_price
        if resistance is not None and 0 < resistance < cost_aware_target:
            min_distance = LIMITS.min_resistance_distance_ratio * stop_distance
            if (resistance - entry_price) >= min_distance:
                target_price = round(float(resistance), 4)
                target_flags.append("TARGET_CAPPED_BY_RESISTANCE")
            else:
                resistance_pct = (resistance - entry_price) / entry_price * 100 if entry_price else 0.0
                min_pct = min_distance / entry_price * 100 if entry_price else 0.0
                console.print(
                    f"[dim]R1 ₹{resistance:.2f} is {resistance_pct:.2f}% above entry "
                    f"(< {min_pct:.2f}% minimum), ignoring — using cost-aware target "
                    f"₹{cost_aware_target:.2f}[/dim]")

        return stop_price, target_price, target_flags

    _weekly_trend_cache: dict[tuple[str, str], str] = {}

    def _weekly_trend(self, symbol: str) -> str:
        from datetime import date

        cache_key = (symbol, date.today().isoformat())
        cached = self._weekly_trend_cache.get(cache_key)
        if cached is not None:
            return cached
        trend = self._weekly_trend_uncached(symbol)
        self._weekly_trend_cache[cache_key] = trend
        return trend

    def _weekly_trend_uncached(self, symbol: str) -> str:
        wdf = self.fetcher.get_price_history(symbol, period="1y", interval="1wk")
        if wdf is None or wdf.empty:
            return "SIDEWAYS"
        wind = compute_indicators(wdf)
        price = float(wdf["Close"].iloc[-1])
        ma50 = wind.get("ma_50")
        if ma50 is None:
            return "SIDEWAYS"
        if price > ma50 * 1.01:
            return "UPTREND"
        if price < ma50 * 0.99:
            return "DOWNTREND"
        return "SIDEWAYS"

    def analyze(self, state: TradeState) -> dict:
        symbol = state["symbol"]
        df = self.fetcher.get_price_history(symbol)
        indicators = compute_indicators(df)

        if df is None or df.empty:
            return {
                "score": 0.0, "signal": "SKIP", "entry_price": 0.0, "stop_price": 0.0,
                "target_price": 0.0, "patterns": [], "indicators": indicators,
                "weekly_trend": "SIDEWAYS", "proceed": False, "flags": [],
                "reasoning": "No price data available.",
            }

        from src.data.watchlist import ALL_STOCKS

        tier = ALL_STOCKS.get(symbol, {}).get("tier", "large")
        entry_price = round(float(df["Close"].iloc[-1]), 4)
        stop_price, target_price, target_flags = self._levels(entry_price, indicators, tier)
        weekly_trend = self._weekly_trend(symbol)
        market_context = state.get("market_context") or self.market.get_nifty_context()
        entry_recommendation = recommend_entry(indicators, entry_price)

        # Strategy library: pick the best strategy for the current regime. If it fires
        # a BUY, its levels/signal take precedence (regime-appropriate setup).
        regime = market_context.get("regime", "TRANSITIONAL")
        strat_ctx = {"pairs_opportunity": state.get("pairs_opportunity")}
        strat_signal = StrategyRegistry().best_signal(
            symbol, df, indicators, strat_ctx, regime
        )
        strategy_name = strat_signal.get("strategy_name", "momentum")
        if strategy_name == "none":
            # No library strategy (momentum/mean_reversion/breakout/pairs)
            # fired a BUY of its own, but the core technical logic below can
            # still produce a BUY from entry_recommendation/judge — tag it
            # from indicators rather than leaving it "none" in the journal.
            from src.analytics.strategy_attribution import classify_strategy

            strategy_name = classify_strategy(indicators)
        if strat_signal.get("signal") == "BUY":
            entry_price = strat_signal["entry_price"]
            stop_price = strat_signal["stop_price"]
            target_price = strat_signal["target_price"]

        flags: list[str] = list(target_flags)
        if indicators.get("adx_signal") == "CHOPPY":
            flags.append("CHOPPY_MARKET")

        # Circuit-breaker volatility risk (HIGH → halve size downstream).
        from src.risk.circuit_breaker_check import CircuitBreakerRisk
        circuit = CircuitBreakerRisk().assess_risk(symbol, df)
        if circuit.get("risk_level") == "HIGH":
            flags.append("HIGH_VOLATILITY")

        # Optimal entry window (used for countdown + auto-cancel-if-missed).
        provisional = {"signal": "BUY", "indicators": indicators}
        time_window = TradingTimeWindow().compute_entry_window(provisional, state)

        # Pairs trades are market-neutral: the buy_symbol's own RSI/ADX/pivots/
        # supertrend describe its individual momentum, not the spread that's
        # actually being traded (spread divergence, already verified by
        # PairsTradingStrategy at 2.0+ sigma). Return here, before any of that
        # gets folded into flags/score — a CHOPPY_MARKET or BELOW_ALL_SUPPORTS
        # flag on the buy leg says nothing about whether the pair reverts.
        if strategy_name == "pairs_trading" and strat_signal.get("signal") == "BUY":
            return {
                "score": strat_signal["score"],
                "signal": "BUY",
                "entry_price": entry_price,
                "stop_price": stop_price,
                "target_price": target_price,
                "patterns": [],
                "proceed": True,
                "strategy_name": "pairs_trading",
                "weekly_trend": weekly_trend,
                "entry_recommendation": entry_recommendation,
                "time_window": time_window,
                "circuit_breaker": circuit,
                "flags": [],
                "indicators": {"rsi_14": indicators.get("rsi_14"), "trend": "SIDEWAYS"},
                "reasoning": (
                    f"Pairs trade — {strat_signal.get('rationale', '')}. "
                    "Individual-stock momentum checks (RSI/ADX/pivots/supertrend) "
                    "are intentionally skipped: this is a market-neutral spread "
                    "position, not a directional bet on this stock alone."
                ),
            }

        base = {
            "entry_price": entry_price,
            "stop_price": stop_price,
            "target_price": target_price,
            "indicators": indicators,
            "weekly_trend": weekly_trend,
            "entry_recommendation": entry_recommendation,
            "time_window": time_window,
            "strategy_name": strategy_name,
            "circuit_breaker": circuit,
            "flags": flags,
        }

        # Recall specific past memories resembling this exact setup — the
        # micro-level complement to AdaptiveThresholds' macro regime strictness.
        from src.memory.pattern_matcher import PatternMatcher

        recall = PatternMatcher().recall(symbol, indicators, regime)

        # Multi-timeframe confirmation: a daily uptrend that the weekly chart
        # disagrees with is a counter-trend bounce, not a trend continuation.
        daily_trend = indicators.get("trend", "SIDEWAYS")
        mtf_adjustment = 0.0
        mtf_note = None
        if daily_trend == "UPTREND" and weekly_trend == "UPTREND":
            mtf_adjustment = 0.1
            mtf_note = "MTF aligned"
        elif daily_trend == "UPTREND" and weekly_trend == "DOWNTREND":
            mtf_adjustment = -0.15
            mtf_note = "MTF_CONFLICT"
            flags.append("MTF_CONFLICT")

        def _apply_recall(verdict: dict) -> dict:
            verdict["memory_recall"] = recall
            verdict["reasoning"] = (verdict.get("reasoning", "") + f" | {recall['summary']}")
            # recall['adjustment'] is on a 0-10 scale; this verdict's score is 0-1.
            score = (verdict.get("score", 0) or 0) + recall["adjustment"] / 10 + mtf_adjustment
            verdict["score"] = round(max(0.0, min(1.0, score)), 4)
            if mtf_note:
                verdict["reasoning"] = verdict["reasoning"] + f" | {mtf_note}"
            return verdict

        def _enforce_weekly(verdict: dict) -> dict:
            # Never fight the weekly trend: downgrade a BUY to HOLD in a weekly downtrend.
            if weekly_trend == "DOWNTREND" and verdict.get("signal") == "BUY":
                verdict["signal"] = "HOLD"
                verdict["proceed"] = False
                vflags = list(verdict.get("flags", []))
                if "WEEKLY_TREND_CONFLICT" not in vflags:
                    vflags.append("WEEKLY_TREND_CONFLICT")
                verdict["flags"] = vflags
                verdict["reasoning"] = (
                    "Downgraded BUY→HOLD: weekly trend is DOWNTREND. "
                    + verdict.get("reasoning", "")
                )
            return verdict

        if settings.effective_demo_mode:
            # DEMO: prefer the regime-selected strategy; fall back to rule-based score.
            if strat_signal.get("signal") == "BUY":
                verdict = {
                    "score": strat_signal["score"], "signal": "BUY", "patterns": [],
                    "proceed": True,
                    "reasoning": f"Strategy: {strategy_name} — {strat_signal['rationale']}",
                }
            else:
                verdict = rule_based_technical_score(
                    indicators, entry_price, stop_price, target_price
                )
                verdict["reasoning"] = f"Strategy: {strategy_name} — " + verdict.get("reasoning", "")
            verdict.update(base)
            verdict = _apply_recall(verdict)
            return _enforce_weekly(verdict)

        fundamental = state.get("fundamental_verdict", {})
        # Context blocks A (knowledge) + B (lessons) + C (recent RCAs) + signal accuracy.
        sector = state.get("sector")
        knowledge_context = state.get("knowledge_context") or ""
        lessons = state.get("lessons") or self.lessons.get_relevant_lessons(symbol, sector)
        rca_context = self.lessons.get_recent_rca_context(symbol, sector)
        signal_context = SignalTracker().get_best_signals(sector)
        system = _SYSTEM
        blocks = []
        if knowledge_context:
            blocks.append(knowledge_context)
        if lessons:
            blocks.append(f"What the system has learned:\n{lessons}")
        if rca_context:
            blocks.append(rca_context)
        if signal_context:
            blocks.append(signal_context)
        if blocks:
            system = _SYSTEM + "\n\n" + "\n\n".join(blocks)

        prompt = (
            f"Symbol: {symbol}\n"
            f"Entry: {entry_price}  Stop: {stop_price} (ATR-based)  Target: {target_price} "
            "(fixed, do not change)\n"
            f"Indicators (authoritative): {indicators}\n"
            f"Candlestick pattern: {indicators.get('candlestick_pattern')} "
            f"(confidence {indicators.get('candlestick_confidence')})\n"
            f"OBV trend: {indicators.get('obv_trend')} | ADX signal: {indicators.get('adx_signal')}\n"
            f"Ichimoku: {indicators.get('ichimoku_signal')} — cloud is {indicators.get('ichimoku_cloud_color')}\n"
            f"VWAP: Price {indicators.get('price_vs_vwap')} VWAP at {indicators.get('vwap')}\n"
            f"Pivots: P={indicators.get('pivot')} R1={indicators.get('r1')} S1={indicators.get('s1')} "
            f"R2={indicators.get('r2')} S2={indicators.get('s2')}\n"
            f"CMF: {indicators.get('cmf_signal')} ({indicators.get('cmf_20')})\n"
            f"Supertrend: {indicators.get('supertrend_direction')} at {indicators.get('supertrend')}\n"
            f"52W Position: {indicators.get('week52_position')} "
            f"({indicators.get('pct_from_52w_high')}% from high)\n"
            f"Weekly trend: {weekly_trend}\n"
            f"Market context: {market_context.get('context_summary')}\n"
            f"Fundamental score: {fundamental.get('score')} proceed={fundamental.get('proceed')}\n\n"
            "Answer these explicitly in your reasoning: Is there a clear entry trigger "
            "visible in the pattern/indicators? Is the weekly trend supportive or working "
            "against this setup? What is the most critical support level below the current price?\n\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "score": <float 0-1>,\n'
            '  "signal": "BUY" | "HOLD" | "SKIP",\n'
            '  "patterns": [<str>],\n'
            '  "critical_support": <float or null>,\n'
            '  "proceed": <bool>,\n'
            '  "reasoning": "<concise; note entry trigger, weekly alignment, support>"\n'
            "}"
        )

        try:
            verdict = parse_json_response(cached_llm_call(system, prompt))
        except Exception as exc:
            console.print(f"[yellow]Technical LLM call failed: {exc}[/yellow]")
            verdict = {}

        if not verdict:
            verdict = {
                "score": 0.5, "signal": "SKIP", "patterns": [], "proceed": False,
                "reasoning": "LLM response unparseable; defaulting to SKIP.",
            }
        verdict.update(base)
        # Merge any LLM-emitted flags with code-derived flags.
        verdict["flags"] = list({*flags, *verdict.get("flags", [])})
        verdict = _apply_recall(verdict)
        return _enforce_weekly(verdict)
