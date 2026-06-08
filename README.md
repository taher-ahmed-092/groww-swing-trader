# groww-swing-trader

AI-driven personal swing-trading system for NSE/BSE via the Groww Trading API. A
multi-agent pipeline researches a stock, computes fundamentals and technicals with
deterministic code, has an LLM "judge" critique the setup, enforces hard risk
limits, pauses for human approval, then executes — paper by default, live only when
deliberately enabled. Personal use only, not a commercial product.

## Architecture

```
                ┌─────────┐
                │  SCOUT  │  screen Nifty-50 watchlist (code-scored)
                └────┬────┘
                     ▼
              ┌────────────┐
              │ FUNDAMENTAL│  code computes ratios → LLM interprets (SWOT, moat)
              └─────┬──────┘
                    ▼
              ┌────────────┐
              │ TECHNICAL  │  code computes indicators → LLM confirms signal
              └─────┬──────┘
                    ▼
                ┌───────┐
                │ JUDGE │  skeptical LLM scores risk/reward, can veto
                └───┬───┘
                    ▼
                ┌───────┐
                │ RISK  │  hard limits: stop, confidence, sizing, exposure
                └───┬───┘
                    ▼
            ┌───────────────┐
            │ HUMAN APPROVE │  LangGraph interrupt (live mode only)
            └───────┬───────┘
                    ▼
              ┌──────────┐
              │ EXECUTOR │  PaperBroker (default) or GrowwBroker (GTT OCO stop+target)
              └────┬─────┘
                   ▼
             ┌────────────┐
             │ REFLECTION │  post-trade lesson → journal → future context
             └────────────┘
```

State flows through a LangGraph `StateGraph` with a SQLite checkpointer (audit
trail + memory). Every node checks the `KILL_SWITCH` file first.

## Quick start — no API keys needed for paper mode

```bash
uv sync
uv run python -m src.orchestrator.graph
```

Paper mode logs simulated trades and places no real orders. It works with zero
credentials — the LLM agents fall back to mock verdicts when no Anthropic key is set.

## Getting real keys

Copy the template and fill it in (each variable is documented with where to get it):

```bash
cp .env.example .env
```

You need an Anthropic API key (LLM agents), a Groww Trading API subscription
(₹499/month), and — required by SEBI — a **whitelisted static IP** for order
placement. See the comments in `.env.example`.

## Risk principles (never bypassed)

1. **Paper first.** `live_trading_enabled` defaults to `False`; never flipped in code.
2. **Every trade has a stop-loss.** The live broker attaches a GTT OCO (stop + target).
3. **Full pipeline always** — even a user-requested trade runs risk → judge → approval.
4. **LLMs reason, code computes.** No LLM ever invents a ratio or indicator value.
5. **Kill switch.** A `KILL_SWITCH` file in the repo root halts all execution.
6. **`min_confidence = 0.80` floor.** A model claiming 1.0 confidence is flagged.
7. **Risk constants live only in `config/risk_limits.py`.** Never hardcoded elsewhere.

## Folder structure

```
config/                 frozen risk limits + Pydantic settings (.env)
src/orchestrator/       LangGraph state machine (graph.py) + state schema
src/agents/scout/       watchlist screening / candidate discovery
src/agents/fundamental/ ratios.py (code) + agent.py (LLM interpretation)
src/agents/technical/   indicators.py (code) + agent.py (LLM confirmation)
src/agents/executor/    broker selection + order placement + journaling
src/judge/              LLM-as-judge trade evaluator
src/broker/             BrokerBase, PaperBroker (default), GrowwBroker (live)
src/risk/               pre-trade risk checker (the hard gate)
src/memory/             SQLite trade journal + post-trade reflection
src/data/               yfinance market data fetcher (no keys required)
src/portfolio/          capital ledger + compounding tracker
src/backtest/           backtesting.py harness (baseline strategy)
scripts/                health_check.py
tests/                  risk checker tests
data/journal/           SQLite DBs (trades, portfolio, checkpoints) — gitignored
logs/                   runtime logs — gitignored
```

## Running tests

```bash
uv run pytest tests/ -v
```

## Health check

```bash
uv run python scripts/health_check.py
```
