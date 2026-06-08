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

from config.risk_limits import LIMITS
from config.settings import settings
from src.agents.executor.agent import ExecutorAgent
from src.agents.fundamental.agent import FundamentalAgent
from src.agents.scout.agent import ScoutAgent
from src.agents.technical.agent import TechnicalAgent
from src.judge.evaluator import LLMJudge
from src.orchestrator.state import TradeState, get_initial_state
from src.risk.checker import RiskChecker

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
def scout_node(state: TradeState) -> dict:
    candidates = ScoutAgent().scan()
    return {"candidates": candidates, "current_step": "scout"}


@kill_switch_guard
def fundamental_node(state: TradeState) -> dict:
    verdict = FundamentalAgent().analyze(state)
    return {"fundamental_verdict": verdict, "current_step": "fundamental"}


@kill_switch_guard
def technical_node(state: TradeState) -> dict:
    verdict = TechnicalAgent().analyze(state)
    return {"technical_verdict": verdict, "current_step": "technical"}


@kill_switch_guard
def judge_node(state: TradeState) -> dict:
    verdict = LLMJudge().evaluate(state)
    return {"judge_verdict": verdict, "current_step": "judge"}


@kill_switch_guard
def risk_node(state: TradeState) -> dict:
    result = RiskChecker().check(state)
    update: dict = {"risk_check": result, "current_step": "risk"}

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
    return {"trade_result": result, "current_step": "executor"}


@kill_switch_guard
def reflection_node(state: TradeState) -> dict:
    # Full post-trade reflection happens after a position closes (see
    # src/memory/reflection.py). At pipeline end we record a short trace note.
    result = state.get("trade_result", {})
    note = (
        f"Pipeline complete for {state.get('symbol')}: "
        f"status={result.get('status', 'N/A')}, mode={result.get('broker_mode', 'N/A')}."
    )
    return {"reflection": note, "current_step": "done"}


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

    graph.add_node("scout_node", scout_node)
    graph.add_node("fundamental_node", fundamental_node)
    graph.add_node("technical_node", technical_node)
    graph.add_node("judge_node", judge_node)
    graph.add_node("risk_node", risk_node)
    graph.add_node("executor_node", executor_node)
    graph.add_node("reflection_node", reflection_node)

    graph.add_edge(START, "scout_node")

    sequence = [
        ("scout_node", "fundamental_node"),
        ("fundamental_node", "technical_node"),
        ("technical_node", "judge_node"),
        ("judge_node", "risk_node"),
        ("risk_node", "executor_node"),
        ("executor_node", "reflection_node"),
    ]
    for current, nxt in sequence:
        graph.add_conditional_edges(current, _make_router(nxt), {nxt: nxt, END: END})

    graph.add_edge("reflection_node", END)
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
