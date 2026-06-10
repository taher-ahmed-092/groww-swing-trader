"""LangGraph shared state schema for the trading pipeline."""
from __future__ import annotations

from typing import TypedDict


class TradeState(TypedDict, total=False):
    symbol: str
    candidates: list[dict]           # from scout: [{symbol, name, rationale, sector}]
    fundamental_verdict: dict        # {score, strengths, weaknesses, swot, moat, proceed, reasoning}
    technical_verdict: dict          # {score, signal, entry_price, stop_price, target_price,
                                     #  patterns, indicators, proceed, reasoning}
    judge_verdict: dict              # {approved, score, reasoning, flags}
    risk_check: dict                 # {approved, position_size_inr, risk_inr, quantity, reasons}
    trade_decision: dict             # final order spec before execution
    trade_result: dict               # after execution: {order_id, status, fill_price, broker_mode}
    reflection: str                  # post-trade LLM reflection
    errors: list[str]
    current_step: str
    manually_requested: bool         # True if user bypassed scout and requested this trade directly
    market_context: dict             # from MarketContext.get_nifty_context()
    lessons: str                     # from LessonsRetriever, injected into agent prompts
    sector: str                      # from scout candidate, carried through state
    sentiment: dict                  # from SocialSentimentAgent (inside FundamentalAgent)
    knowledge_context: str           # from KnowledgeBase, injected once per run
    thread_id: str                   # LangGraph thread_id, for journal traceability
    entry_recommendation: dict       # from recommend_entry() in TechnicalAgent
    gut_check: dict                  # from GutCheckAgent (holistic assessment)
    sizing_details: dict             # from PositionSizer (Kelly/confidence/regime)
    time_window: dict                # from TradingTimeWindow (optimal entry window)


def get_initial_state(symbol: str) -> TradeState:
    """Build a fresh, fully-initialized TradeState for a pipeline run."""
    return TradeState(
        symbol=symbol,
        candidates=[],
        fundamental_verdict={},
        technical_verdict={},
        judge_verdict={},
        risk_check={},
        trade_decision={},
        trade_result={},
        reflection="",
        errors=[],
        current_step="initialized",
        manually_requested=False,
        market_context={},
        lessons="",
        sector="",
        sentiment={},
        knowledge_context="",
        thread_id="",
        entry_recommendation={},
        gut_check={},
        sizing_details={},
        time_window={},
    )
