# groww-swing-trader — context for Claude Code

Personal swing-trading system. NSE/BSE via Groww API. NOT a professional product.

## Non-negotiable rules (never violate, never bypass)
1. PAPER FIRST. `live_trading_enabled` defaults False. Never flip it in code.
2. EVERY trade has a stop-loss. No exceptions.
3. EVERY trade — autonomous OR user-requested — passes the full pipeline:
   risk checks -> LLM-as-judge -> sizing. No "just place this order" shortcut.
4. LLMs narrate and reason; they NEVER compute ratios/indicators or invent numbers.
   All numeric analysis comes from deterministic code over real data.
5. No "100% confidence" — system is probabilistic. Size down as confidence drops.
6. If file `KILL_SWITCH` exists in repo root, halt all execution immediately.
7. All risk constants live in config/risk_limits.py. Never hardcode elsewhere.

## Architecture
- Orchestrator: LangGraph (state machine + checkpointer = audit trail + memory).
- Agent 1 scout: weekly screening / candidate discovery.
- Agent 2 fundamental: code computes ratios; LLM interprets (SWOT, moat, risks).
- Agent 3 technical: code computes indicators; LLM confirms vs. fundamentals.
- Judge: scores each proposed trade vs. rubric, can veto.
- Executor: Groww API (paper adapter by default), attaches GTT/OCO stop+target.
- Memory: trade journal + post-trade reflection -> lessons retrieved as context.
- Portfolio: ledger, capital tracking, compounding.

## Always run backtest + paper before proposing live changes.