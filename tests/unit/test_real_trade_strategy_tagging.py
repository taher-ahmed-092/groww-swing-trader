"""Real trades whose entry comes from the technical agent's own scoring (not
one of the four library strategies in src/strategies/) previously got
strategy_name="none" in the journal/Hangar — StrategyRegistry.best_signal's
SKIP sentinel ("no strategy found a setup in this regime") leaked straight
into the trade record. TechnicalAgent.analyze now falls back to
classify_strategy(indicators) whenever the library returns "none", so every
real trade still gets tagged with an actual style."""
from __future__ import annotations

import pandas as pd

from src.agents.technical import agent as agent_module
from src.agents.technical.agent import TechnicalAgent
from src.orchestrator.state import get_initial_state


def _fixed_indicators() -> dict:
    return {
        "rsi_14": 55, "trend": "UPTREND", "adx_signal": "TRENDING",
        "ma_200": 100.0, "close": 120.0, "volume_ratio": 1.0,
        "flags": [], "atr_stop": 114.0, "atr_14": 2.0,
    }


def _skip_signal() -> dict:
    return {
        "signal": "SKIP", "score": 0.0, "strategy_name": "none",
        "entry_price": 120.0, "stop_price": 0.0, "target_price": 0.0,
        "rationale": "No strategy found a setup in this regime",
    }


def test_real_trade_with_no_library_strategy_tags_momentum_not_none(monkeypatch):
    agent = TechnicalAgent()
    df = pd.DataFrame({
        "Close": [118, 119, 120], "High": [119, 120, 121], "Low": [117, 118, 119],
        "Open": [117, 118, 119], "Volume": [1000, 1000, 1000],
    })

    monkeypatch.setattr(agent_module, "compute_indicators", lambda _df: _fixed_indicators())
    monkeypatch.setattr(agent.fetcher, "get_price_history", lambda *a, **k: df)
    monkeypatch.setattr(agent.market, "get_nifty_context", lambda: {"regime": "BULL_TRENDING"})
    monkeypatch.setattr(agent, "_weekly_trend", lambda symbol: "UPTREND")

    from src.strategies.registry import StrategyRegistry
    monkeypatch.setattr(
        StrategyRegistry, "best_signal",
        lambda self, symbol, df, indicators, context, regime: _skip_signal(),
    )
    from src.risk.circuit_breaker_check import CircuitBreakerRisk
    monkeypatch.setattr(CircuitBreakerRisk, "assess_risk", lambda self, symbol, df: {"risk_level": "LOW"})
    from src.memory.pattern_matcher import PatternMatcher
    monkeypatch.setattr(
        PatternMatcher, "recall",
        lambda self, symbol, indicators, regime: {"summary": "no prior memory", "adjustment": 0},
    )

    from config.settings import settings
    monkeypatch.setattr(type(settings), "effective_demo_mode", property(lambda self: True))

    state = get_initial_state("TESTCO")
    verdict = agent.analyze(state)

    assert verdict["strategy_name"] == "momentum"
    assert verdict["strategy_name"] != "none"
