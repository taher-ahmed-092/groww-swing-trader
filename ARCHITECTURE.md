# Architecture

Personal AI-driven swing-trading system for NSE/BSE (see `CLAUDE.md` for the
non-negotiable rules). This file documents the parts of the system that don't
fit in a one-line CLAUDE.md summary.

## Pipeline

```
scout_node → fundamental_node → technical_node → judge_node → risk_node →
  interrupt(human_approval) → executor_node → reflection_node
```

- **Scout** (`src/agents/scout/agent.py`) — deterministic multi-dimension
  scoring across the 200+ stock watchlist: momentum, trend, volume, 1m/20d/30d
  relative strength vs Nifty, institutional flow, 52W position, sector
  knowledge. The 30d relative-strength dimension boosts leaders outperforming
  Nifty by >3% and penalizes laggards (>5% behind) — leaders in a weak market
  tend to become the biggest winners when it turns.
- **Technical** (`src/agents/technical/agent.py`) — indicators + entry/stop/
  target are pure code (CLAUDE.md rule 4); the LLM only interprets. Adds a
  multi-timeframe (daily+weekly, weekly cached per symbol/day) confirmation:
  +0.1 score when both align, -0.15 and an `MTF_CONFLICT` flag when the daily
  uptrend fights the weekly trend (counter-trend bounce risk).
- **Pattern Matcher** (`src/memory/pattern_matcher.py`) — recalls SPECIFIC
  past memories resembling the current setup (same symbol, or same regime +
  trend), weighted by each memory's own confidence, and applies a bounded
  ±1.5 adjustment to the technical score. This is the micro-level complement
  to `AdaptiveThresholds` (which only adjusts overall regime strictness).
- **Judge** (`src/judge/evaluator.py`) — auto-vetoes pre-LLM (hard-rejected
  fundamentals, poor R:R, choppy market, fighting the trend, and now
  `COSTS_EAT_EDGE` — see Cost Model below), then a weighted LLM scorecard.
  The approval threshold blends the trading mode's base bar with
  `AdaptiveThresholds`' regime-learned bar, bounded to mode ± 1.5.
- **Risk** (`src/risk/checker.py`) — position sizing (Kelly × confidence ×
  regime × tier), sector/position caps, and a **daily loss circuit breaker**:
  if today's cumulative NET P&L across all sources (real + forced + intraday
  + short) drops below -6%, all trading halts until the date changes
  (`data/cache/daily_loss_halt.txt`), with a one-time Telegram alert.
- **Executor** (`src/agents/executor/agent.py`) — paper fills now include
  tier-based slippage (`fill_price = signal_price * (1 + slippage)`); both
  prices are journaled so cost analysis reflects reality.

## Cost Model (`src/trading/cost_model.py`)

Every simulated and real trade's P&L is optimistic fiction without transaction
costs. `compute_round_trip_costs()` models the full Indian equity cost stack:

| Component | Delivery | Intraday |
|---|---|---|
| Brokerage | ₹0 (Groww) | min(₹20, 0.05%) per side |
| STT | 0.1% both sides | 0.025% sell side |
| Exchange (NSE) | 0.00297% | same |
| Stamp duty | 0.015% buy side | same |
| GST | 18% of (brokerage + exchange) | same |
| Slippage | tier-based: 0.05% large / 0.10% mid / 0.15% small, per side | same |

Every closed trade (real `TradeRecord`, forced/historical-sim, intraday sim)
now carries both `pnl_pct` (net) and `gross_pnl_pct` — the outcome (WIN/LOSS)
is still determined by whether the gross price hit target/stop, but the P&L
shown everywhere is net. A trade that hits target gross-positive but nets
negative is tagged `COSTS_ATE_PROFIT` in RCA (`src/memory/rca.py`) — if it
recurs, the knowledge base learns "tight-target trades don't clear Indian
costs — widen targets."

## Professional Analytics (`src/analytics/performance.py`)

`get_professional_metrics()` computes the numbers professional platforms lead
with, across the shared merged trade history
(`src/analytics/trade_loader.py`, real + forced + intraday + short):

- **Profit factor** — gross wins / gross losses (capped at 999.99, not
  `inf`, since `Infinity` isn't valid JSON and would silently break the
  dashboard's `JSON.parse()`).
- **Max drawdown %** — largest peak-to-trough drop on the cumulative equity
  curve.
- **Strategy decay** — rolling 20-trade win rate vs the prior 20; a >15pp
  drop fires `decay_alert` and a Telegram notification from
  `daily_learning_job` (`runner.py`).

## XGBoost Walk-Forward Validation (`src/ml/signal_combiner.py`)

Training no longer fits all 30+ closed trades directly. It splits
chronologically (first 70% train, last 30% validate); the model is only
saved if validation accuracy exceeds 55%. Below that, `train()` returns
`{"trained": False, "reason": "..."}` rather than deploying a model that
fit historical noise and would collapse live — the classic overfitting/decay
trap.

## Telegram Commands

Consolidated from ~40 overlapping commands to 17 flagship ones
(`src/notifications/command_handler.py`). Every retired command still
resolves — it sends a one-line redirect note, then runs the new handler
with the same args, so no muscle memory breaks.

| Command | Absorbs |
|---|---|
| `/report` | `/status /health /performance /totals /funds /strategies` — now leads with net expectancy, profit factor, max drawdown, decay trend |
| `/scan` (`pairs`) | `/pairs` |
| `/positions` | `/portfolio` |
| `/trades` (`wins`/`losses`) | `/wins /lessons` |
| `/learn` (`full`) | `/knowledge /learning /thresholds /signals /evaluation /lessons_file /enhance /params` |
| `/chart` (`wins`) | `/win_chart` |
| `/mode` | `/conserve /balanced /rogue` (now inline buttons via callback_query) |
| `/trade` | `/force_now /forced` |
| `/pause` | `/resume` (now a toggle) |
| `/dashboard` | `/dashboard_link` |

`/kill` and `/reset_kill` stay separate and unmerged — safety must be an
explicit, deliberate act.

## Dashboard (`dashboard/server.py`, `dashboard/dashboard.html`)

FastAPI + WebSocket, token-gated, localhost-bound. `collect_dashboard_data()`
merges everything above into one payload: `combined_totals` (with gross/net
expectancy), `professional_metrics` (profit factor, drawdown, decay trend),
`adaptive_thresholds`, `funny_line` (regime-grounded humor). The front end is
a candy-space game aesthetic — animated sky background, gyroscope tilt,
flying-plane trade animations color-coded by source, click-anywhere plane
crash, a scoreboard strip, and a loss-diagnosis table per regime.
