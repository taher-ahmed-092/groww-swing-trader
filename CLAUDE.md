# groww-swing-trader

Personal swing-trading system for NSE/BSE via Groww API. Personal use only, not commercial.

## What this is
AI-driven multi-agent system: research → fundamental analysis → technical analysis → LLM judge → risk check → human approval → execution → post-trade reflection. Default mode is paper trading (no real orders).

## Tech stack
- Orchestration: LangGraph (src/orchestrator/graph.py) — StateGraph with SqliteSaver checkpointer
- Agents: scout → fundamental → technical → judge → executor
- LLM: claude-sonnet-4-20250514 via langchain-anthropic (never hardcode model strings elsewhere)
- Broker: PaperBroker (default) or GrowwBroker (requires LIVE_TRADING_ENABLED=true + keys)
- Storage: SQLite via SQLModel at data/journal/trades.db
- Config: config/settings.py (Pydantic BaseSettings from .env) + config/risk_limits.py (frozen constants)
- Data: yfinance (free, no key needed) + Groww historical API (requires key)
- TA: ta library (computed in src/agents/technical/indicators.py — pure code, no LLM)

## NON-NEGOTIABLE RULES — never bypass these in any code you write
1. PAPER FIRST. settings.live_trading_enabled defaults to False everywhere. Never flip it in code.
2. Every trade gets a stop-loss. GrowwBroker.place_order() MUST attach a GTT OCO (stop + target legs).
3. Every trade — autonomous or manually triggered by the user — passes the full pipeline:
   risk_checker → judge → [interrupt: human_approval] → executor. Zero shortcuts.
4. LLMs interpret and reason. They never compute financial ratios or TA indicator values.
   All numeric analysis is deterministic code over real data (ratios.py, indicators.py).
5. If file KILL_SWITCH exists in repo root: halt all execution immediately in every node.
6. min_confidence = 0.80 is the floor. A model outputting confidence=1.0 is hallucinating — flag it.
7. Risk constants live ONLY in config/risk_limits.py. Never hardcode them elsewhere.
8. When user says "take this trade" — run it through the full pipeline including judge. Never skip
   because the user asked. Flag as MANUALLY_REQUESTED in judge context. Anti-emotional-trade rule.

## Agent pipeline (LangGraph node order)
scout_node → fundamental_node → technical_node → judge_node → risk_node → interrupt(human_approval) → executor_node → reflection_node

## Key commands
- Run paper mode: uv run python -m src.orchestrator.graph
- Run tests:      uv run pytest tests/ -v
- Add dependency: uv add <package>
- Health check:   uv run python scripts/health_check.py

## Key files to know
- Entry point:    src/orchestrator/graph.py
- State schema:   src/orchestrator/state.py
- Risk constants: config/risk_limits.py
- Settings/env:   config/settings.py
- Pre-trade check: src/risk/checker.py
- Judge:          src/judge/evaluator.py
- Broker base:    src/broker/base.py
- Paper broker:   src/broker/paper.py
- Journal:        src/memory/journal.py

## Getting started once keys exist
1. cp .env.example .env  →  fill in values (see .env.example for where to get each key)
2. uv sync
3. uv run python -m src.orchestrator.graph  (paper mode — safe, no real orders)
4. Only after paper mode shows consistent edge over 20+ trades: set LIVE_TRADING_ENABLED=true
