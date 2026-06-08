"""
Technical agent.

Code computes the indicators (indicators.py) AND the deterministic entry/stop/target
levels from config.risk_limits. The LLM only interprets indicator values and emits a
signal — it never computes numbers (CLAUDE.md rule 4).
"""
from __future__ import annotations

from rich.console import Console

from config.risk_limits import LIMITS
from config.settings import settings
from src.agents.technical.indicators import compute_indicators
from src.data.fetcher import MarketDataFetcher
from src.llm import get_llm, parse_json_response
from src.orchestrator.state import TradeState

console = Console()

_SYSTEM = (
    "You are a technical analyst. You are given pre-computed indicator values and "
    "fixed entry/stop/target levels. Interpret the setup and emit a signal. You MUST "
    "NOT invent or recompute any numbers — reason only from the values provided."
)


class TechnicalAgent:
    def __init__(self) -> None:
        self.fetcher = MarketDataFetcher()

    def _levels(self, entry_price: float) -> tuple[float, float]:
        stop_price = round(entry_price * (1 - LIMITS.stop_loss_pct / 100), 4)
        target_price = round(entry_price + (entry_price - stop_price) * LIMITS.target_reward_ratio, 4)
        return stop_price, target_price

    def analyze(self, state: TradeState) -> dict:
        symbol = state["symbol"]
        df = self.fetcher.get_price_history(symbol)
        indicators = compute_indicators(df)

        if df is None or df.empty:
            return {
                "score": 0.0, "signal": "SKIP", "entry_price": 0.0, "stop_price": 0.0,
                "target_price": 0.0, "patterns": [], "indicators": indicators,
                "proceed": False, "reasoning": "No price data available.",
            }

        entry_price = round(float(df["Close"].iloc[-1]), 4)
        stop_price, target_price = self._levels(entry_price)

        base = {
            "entry_price": entry_price,
            "stop_price": stop_price,
            "target_price": target_price,
            "indicators": indicators,
        }

        if not settings.has_anthropic_key:
            return {
                **base,
                "score": 0.5, "signal": "HOLD", "patterns": [], "proceed": True,
                "reasoning": "MOCK — no ANTHROPIC_API_KEY; indicators computed but not interpreted.",
            }

        fundamental = state.get("fundamental_verdict", {})
        prompt = (
            f"Symbol: {symbol}\n"
            f"Entry: {entry_price}  Stop: {stop_price}  Target: {target_price} "
            "(these are fixed, do not change them)\n"
            f"Indicators (authoritative): {indicators}\n"
            f"Fundamental verdict score: {fundamental.get('score')} "
            f"proceed={fundamental.get('proceed')}\n\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "score": <float 0-1>,\n'
            '  "signal": "BUY" | "HOLD" | "SKIP",\n'
            '  "patterns": [<str>],\n'
            '  "proceed": <bool>,\n'
            '  "reasoning": "<concise; note alignment with fundamentals>"\n'
            "}"
        )

        try:
            resp = get_llm(temperature=0).invoke(
                [("system", _SYSTEM), ("human", prompt)]
            )
            verdict = parse_json_response(getattr(resp, "content", "") or "")
        except Exception as exc:
            console.print(f"[yellow]Technical LLM call failed: {exc}[/yellow]")
            verdict = {}

        if not verdict:
            verdict = {
                "score": 0.5, "signal": "SKIP", "patterns": [], "proceed": False,
                "reasoning": "LLM response unparseable; defaulting to SKIP.",
            }
        verdict.update(base)
        return verdict
