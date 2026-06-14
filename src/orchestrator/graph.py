"""
LangGraph StateGraph wiring the full swing-trade pipeline.

Node order (see CLAUDE.md):
    scout -> fundamental -> technical -> judge -> risk
          -> [interrupt: human_approval if live] -> executor -> reflection

Every node checks the KILL_SWITCH file first. If present, it records an error and
the graph routes straight to END — no further nodes run.

Run paper-mode demo:
    uv run python -m src.orchestrator.graph
"""
from __future__ import annotations

import os
import sqlite3
from functools import wraps

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from rich.console import Console

from config.risk_limits import LIMITS
from config.settings import settings
from src.agents.executor.agent import ExecutorAgent
from src.agents.fundamental.agent import FundamentalAgent
from src.agents.gut_check import GutCheckAgent
from src.agents.scout.agent import WATCHLIST, ScoutAgent
from src.agents.technical.agent import TechnicalAgent
from src.analytics.performance import PerformanceAnalyzer
from src.data.market_context import MarketContext
from src.data.regime_detector import RegimeDetector
from src.judge.evaluator import LLMJudge
from src.learning.forward_simulator import ForwardSimulator
from src.memory.journal import TradingJournal
from src.memory.lessons import LessonsRetriever
from src.memory.rca import RootCauseAnalyzer
from src.notifications.telegram_bot import TelegramNotifier
from src.orchestrator.state import TradeState, get_initial_state
from src.risk.checker import RiskChecker

console = Console()

CHECKPOINT_DB = os.path.join("data", "journal", "checkpoints.db")


def _kill_switch_active() -> bool:
    return os.path.exists(LIMITS.kill_switch_file)


def kill_switch_guard(node_fn):
    """Decorator: bail out of a node immediately if the kill switch file exists."""

    @wraps(node_fn)
    def wrapper(state: TradeState) -> dict:
        if _kill_switch_active():
            errors = list(state.get("errors", []))
            errors.append(f"KILL_SWITCH present — halted before {node_fn.__name__}")
            return {"errors": errors, "current_step": "halted"}
        return node_fn(state)

    return wrapper


# ── Nodes ───────────────────────────────────────────────────────────────────
@kill_switch_guard
def market_context_node(state: TradeState) -> dict:
    ctx = MarketContext().get_nifty_context()
    # Enrich with the market regime + its position-size multiplier.
    try:
        regime = RegimeDetector().detect()
        ctx["regime"] = regime.get("regime")
        ctx["regime_strategy"] = regime.get("strategy")
        ctx["size_multiplier"] = regime.get("size_multiplier", 1.0)
    except Exception as exc:
        console.print(f"[yellow][MARKET] regime detection failed: {exc}[/yellow]")
        ctx.setdefault("size_multiplier", 1.0)
    update: dict = {"market_context": ctx, "current_step": "market_context"}
    if not ctx.get("market_safe_to_buy"):
        # Don't block here — the judge factors the downtrend in (and can veto).
        console.print(f"[yellow][MARKET] {ctx.get('context_summary')} | regime={ctx.get('regime')}[/yellow]")
    return update


@kill_switch_guard
def knowledge_context_node(state: TradeState) -> dict:
    # Standing learned patterns, injected once and read by every downstream agent.
    knowledge = TradingJournal().format_knowledge_for_context(min_confidence=0.3)
    return {"knowledge_context": knowledge, "current_step": "knowledge_context"}


@kill_switch_guard
def lessons_node(state: TradeState) -> dict:
    retriever = LessonsRetriever()
    lessons = retriever.get_relevant_lessons(
        state.get("symbol", ""), state.get("sector") or None
    )
    update: dict = {"lessons": lessons, "current_step": "lessons"}
    # Also (re)hydrate knowledge_context from the DB if not already set upstream.
    if not state.get("knowledge_context"):
        update["knowledge_context"] = retriever.journal.format_knowledge_for_context(
            min_confidence=0.3
        )
    return update


@kill_switch_guard
def scout_node(state: TradeState) -> dict:
    candidates = ScoutAgent().scan()
    # Carry the traded symbol's sector through state for downstream agents.
    symbol = state.get("symbol")
    sector = next(
        (c.get("sector") for c in candidates if c.get("symbol") == symbol),
        WATCHLIST.get(symbol, {}).get("sector", ""),
    )
    return {"candidates": candidates, "sector": sector, "current_step": "scout"}


@kill_switch_guard
def fundamental_node(state: TradeState) -> dict:
    verdict = FundamentalAgent().analyze(state)
    return {"fundamental_verdict": verdict, "current_step": "fundamental"}


@kill_switch_guard
def technical_node(state: TradeState) -> dict:
    verdict = TechnicalAgent().analyze(state)
    return {
        "technical_verdict": verdict,
        "entry_recommendation": verdict.get("entry_recommendation", {}),
        "time_window": verdict.get("time_window", {}),
        "current_step": "technical",
    }


@kill_switch_guard
def gut_check_node(state: TradeState) -> dict:
    gut = GutCheckAgent().check(state)
    return {"gut_check": gut, "current_step": "gut_check"}


@kill_switch_guard
def judge_node(state: TradeState) -> dict:
    verdict = LLMJudge().evaluate(state)
    return {"judge_verdict": verdict, "current_step": "judge"}


@kill_switch_guard
def risk_node(state: TradeState) -> dict:
    result = RiskChecker().check(state)
    update: dict = {
        "risk_check": result,
        "sizing_details": result.get("sizing_details", {}),
        "current_step": "risk",
    }

    if result.get("approved"):
        tech = state.get("technical_verdict", {})
        update["trade_decision"] = {
            "symbol": state["symbol"],
            "order_type": "BUY",
            "quantity": result.get("quantity", 0),
            "price": tech.get("entry_price"),
            "stop_price": tech.get("stop_price"),
            "target_price": tech.get("target_price"),
        }
    return update


@kill_switch_guard
def executor_node(state: TradeState) -> dict:
    result = ExecutorAgent().execute(state)
    update: dict = {"trade_result": result, "current_step": "executor"}
    if result.get("status") == "REJECTED_AT_APPROVAL":
        errors = list(state.get("errors", []))
        errors.append(result.get("error", "Trade rejected at human approval gate"))
        update["errors"] = errors
    return update


@kill_switch_guard
def reflection_node(state: TradeState) -> dict:
    # Full post-trade reflection happens after a position closes (see
    # src/memory/reflection.py). At pipeline end we record a short trace note.
    result = state.get("trade_result", {})
    note = (
        f"Pipeline complete for {state.get('symbol')}: "
        f"status={result.get('status', 'N/A')}, mode={result.get('broker_mode', 'N/A')}."
    )
    # If this run produced a closed LOSS, run Root Cause Analysis immediately.
    # (In normal swing flow a trade closes days later via the post-market job, which
    #  also runs RCA; this covers same-run closures and keeps the pipeline complete.)
    if result.get("outcome") == "LOSS":
        snapshot = {
            "fundamental_verdict": state.get("fundamental_verdict", {}),
            "technical_verdict": state.get("technical_verdict", {}),
            "judge_verdict": state.get("judge_verdict", {}),
            "market_context": state.get("market_context", {}),
            "sentiment": state.get("sentiment", {}),
            "sector": state.get("sector", ""),
            "manually_requested": bool(state.get("manually_requested")),
        }
        trade_id = result.get("trade_id")
        if trade_id is not None:
            try:
                journal = TradingJournal()
                trade = journal.get_recent(50)
                match = next((t for t in trade if t.id == trade_id), None)
                if match is not None:
                    RootCauseAnalyzer(journal).analyze(match, snapshot)
            except Exception as exc:
                console.print(f"[yellow]RCA in reflection_node failed: {exc}[/yellow]")
    return {"reflection": note, "current_step": "reflection"}


@kill_switch_guard
def analytics_node(state: TradeState) -> dict:
    should_pause, reason = PerformanceAnalyzer().should_pause_trading()
    update: dict = {"current_step": "done"}
    if should_pause:
        msg = f"⚠️ Trading cool-down advised: {reason}"
        console.print(f"[red][ANALYTICS] {msg}[/red]")
        TelegramNotifier().send_message(msg)
        errors = list(state.get("errors", []))
        errors.append(msg)
        update["errors"] = errors

    # 24/7 forward learning: log every candidate (traded or not) for outcome tracking.
    result = state.get("trade_result", {}) or {}
    status = result.get("status", "")
    decision = "EXECUTED" if status in ("PAPER_FILLED", "LIVE_PLACED") else "REJECTED"
    risk = state.get("risk_check", {}) or {}
    rejection_reason = ""
    if decision == "REJECTED":
        reasons = risk.get("reasons") or state.get("errors") or [""]
        rejection_reason = reasons[0] if reasons else ""
    try:
        ForwardSimulator().log_candidate(state, decision=decision,
                                         rejection_reason=rejection_reason)
    except Exception as exc:
        console.print(f"[yellow]Forward simulation log failed: {exc}[/yellow]")
    return update


# ── Routing ──────────────────────────────────────────────────────────────────
def _make_router(next_node: str):
    """Route to END if halted (kill switch), else to the next node."""

    def router(state: TradeState) -> str:
        if state.get("current_step") == "halted" or _kill_switch_active():
            return END
        return next_node

    return router


def build_graph() -> StateGraph:
    graph = StateGraph(TradeState)

    graph.add_node("market_context_node", market_context_node)
    graph.add_node("knowledge_context_node", knowledge_context_node)
    graph.add_node("lessons_node", lessons_node)
    graph.add_node("scout_node", scout_node)
    graph.add_node("fundamental_node", fundamental_node)
    graph.add_node("technical_node", technical_node)
    graph.add_node("gut_check_node", gut_check_node)
    graph.add_node("judge_node", judge_node)
    graph.add_node("risk_node", risk_node)
    graph.add_node("executor_node", executor_node)
    graph.add_node("reflection_node", reflection_node)
    graph.add_node("analytics_node", analytics_node)

    graph.add_edge(START, "market_context_node")

    sequence = [
        ("market_context_node", "knowledge_context_node"),
        ("knowledge_context_node", "lessons_node"),
        ("lessons_node", "scout_node"),
        ("scout_node", "fundamental_node"),
        ("fundamental_node", "technical_node"),
        ("technical_node", "gut_check_node"),
        ("gut_check_node", "judge_node"),
        ("judge_node", "risk_node"),
        ("risk_node", "executor_node"),
        ("executor_node", "reflection_node"),
        ("reflection_node", "analytics_node"),
    ]
    for current, nxt in sequence:
        graph.add_conditional_edges(current, _make_router(nxt), {nxt: nxt, END: END})

    graph.add_edge("analytics_node", END)
    return graph


def compile_app():
    os.makedirs(os.path.dirname(CHECKPOINT_DB), exist_ok=True)
    # Own the sqlite connection directly so it stays open for the process lifetime.
    # check_same_thread=False keeps it usable if LangGraph touches another thread.
    conn = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    graph = build_graph()

    compile_kwargs: dict = {"checkpointer": checkpointer}
    # Human-in-the-loop: pause before live execution so a human can review.
    if settings.live_trading_enabled:
        compile_kwargs["interrupt_before"] = ["executor_node"]

    return graph.compile(**compile_kwargs)


app = compile_app()


if __name__ == "__main__":
    symbol = "RELIANCE"  # safe, large-cap for demo
    initial_state = get_initial_state(symbol)
    print("[PAPER MODE DEMO] Running pipeline for:", symbol)
    for step in app.stream(initial_state, config={"configurable": {"thread_id": "demo-1"}}):
        for node_name, state_update in step.items():
            print(f"  ✓ {node_name}")
    print("[DONE] Check data/journal/ for logs.")
