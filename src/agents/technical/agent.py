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

    def _levels(self, entry_price: float, indicators: dict) -> tuple[float, float]:
        # Prefer the ATR-based stop; fall back to the % stop if ATR is unavailable
        # or produces a nonsensical (non-positive / above-entry) level.
        atr_stop = indicators.get("atr_stop")
        if atr_stop is not None and 0 < atr_stop < entry_price:
            stop_price = round(float(atr_stop), 4)
        else:
            stop_price = round(entry_price * (1 - LIMITS.stop_loss_pct / 100), 4)
        target_price = round(entry_price + (entry_price - stop_price) * LIMITS.target_reward_ratio, 4)
        return stop_price, target_price

    def _weekly_trend(self, symbol: str) -> str:
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

        entry_price = round(float(df["Close"].iloc[-1]), 4)
        stop_price, target_price = self._levels(entry_price, indicators)
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
        if strat_signal.get("signal") == "BUY":
            entry_price = strat_signal["entry_price"]
            stop_price = strat_signal["stop_price"]
            target_price = strat_signal["target_price"]

        flags: list[str] = []
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
        return _enforce_weekly(verdict)
