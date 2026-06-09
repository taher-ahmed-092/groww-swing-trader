# Paper Trading Guide

This system is **paper-first**. No real orders are placed until you deliberately set
`LIVE_TRADING_ENABLED=true` with valid Groww keys and a whitelisted static IP.

## Modes

| Mode | When | Behavior |
|------|------|----------|
| 🧪 DEMO | no `ANTHROPIC_API_KEY`, or `PAPER_DEMO_MODE=true` | Rule-based scoring (no LLM). Infrastructure testing. |
| 📝 PAPER | `ANTHROPIC_API_KEY` set, live off | Full LLM agents, simulated fills, journaled. |
| 🔴 LIVE | `LIVE_TRADING_ENABLED=true` + Groww keys + static IP | Real GTT-OCO orders, human approval gate. |

`settings.mode_label` shows the current mode everywhere.

## Quick start (zero credentials)

```bash
uv sync
uv run python scripts/run_now.py --scout-only     # top stocks + market regime
uv run python scripts/run_now.py --scan           # paper-trade the top 2 with full analysis
uv run python scripts/run_now.py --symbol RELIANCE # analyze one symbol
uv run python scripts/check_positions.py          # advance stops, close hit trades
uv run python runner.py                            # start the full scheduler
```

## What every trade now includes

- **Dynamic position size** — Kelly × confidence × market-regime multiplier, clamped ₹50–₹500.
- **Entry zone** — recommended entry range; flags when price is extended (wait for pullback).
- **Tiered stops** — hard (ATR-based) → breakeven at +3% → trailing at +5% → partial exit.
- **Gut check** — a holistic veteran-trader assessment after all checklist gates.
- **Signal accuracy context** — your own historical win rate per signal, fed back into analysis.

## The self-improvement cycle (automatic)

```
Every trade   → Root Cause Analysis → KnowledgeBase patterns
Every evening → DailySynthesizer    → daily journal entry
Sunday 7 PM   → WeeklyDistiller     → promotes patterns to rules
Sunday 8 PM   → AgentEvaluator      → accuracy + calibration metrics
Sunday 8:30PM → WorkflowEnhancer    → auto-tunes adaptive_params.json
```

- After ~20 trades: the system learns which signals work for **your** watchlist.
- After ~50 trades: parameter auto-tuning kicks in meaningfully.
- After ~100 trades: calibrated to your market and style.

## Going live (only after a proven paper edge)

1. Paper trade until win rate + expectancy are consistently positive over 20+ trades.
2. Subscribe to Groww Trade API, generate a token, whitelist a static IP.
3. Fill `.env`, set `LIVE_TRADING_ENABLED=true`.
4. Every live order still passes the full pipeline + a Telegram/CLI human approval gate.

**Kill switch:** create a file named `KILL_SWITCH` in the repo root to halt everything immediately.
