# Trading System Audit Report

*Generated: 2026-07-24 09:18 UTC*
*Commit: 9f5e96230772712020a25711470f0269d2ae05f6*

This is a diagnostic-only audit. No trading logic was modified while producing it —
all commands below are read-only inspections and throwaway test invocations (the
kill-switch test creates and immediately removes a `KILL_SWITCH` file).

## Executive Summary

**Overall readiness: NOT YET**

The system is structurally sound on safety (kill switch, stop-loss enforcement,
signal stability, look-ahead bias) but is **not net-profitable after realistic
Indian transaction costs** on the accumulated simulation/forced-trade history, has
**zero real paper trades** recorded in the journal to validate the live pipeline
end-to-end, and its learning data so far is **concentrated in one market regime**.
None of these are reasons to panic — they're exactly what this audit exists to
catch before real money is at risk — but they are reasons to keep paper trading,
not go live.

## Finding 1: Look-Ahead Bias

**Status: CLEAN** (one labeling caveat, not a leak)

- `HistoricalReplayEngine._replay_stock()` correctly slices `df_full.iloc[:idx+1]`
  before computing indicators, and separately uses `df_full.iloc[idx+1:idx+6]` only
  for the (intentional, clearly-labeled) outcome check.
- `compute_indicators()` (`src/agents/technical/indicators.py`) guards every rolling
  window with an explicit `n >= window` check before computing (e.g. `ma_200`
  requires `n >= 200`, `ichimoku` requires `n >= 52`). Verified live: with only 100
  rows of RELIANCE data, `ma_200` correctly returned `None` rather than a value
  computed from an incomplete window. No `shift(-1)` or `center=True` usage anywhere
  in the file — all rolling windows are backward-looking by construction.
- Verified RSI/MACD/ADX differ (as expected) between a 100-row slice and a 110-row
  slice of the same series — confirms the function is genuinely windowed on
  whatever's passed in, not silently using a cached full-history value.
- **Caveat (not a leak, but mislabeled)**: the "52-week high/low" fields
  (`pct_from_52w_high`, `week52_position`, etc.) are computed as `high.max()` /
  `low.min()` over *whatever DataFrame was passed in* — there is no 252-row
  windowing check. During an early replay day with e.g. only 100 rows available,
  this silently reports a 100-day high/low labeled as a 52-week figure. It never
  looks at future data (so it isn't a look-ahead bias), but the field name overstates
  what was actually measured whenever replay runs on a stock's early history.

### Historical-sim outcome methodology (a genuine data-quality finding)

Of 115 `HISTORICAL_SIM` trades in `data/cache/forced_trades_history.json`:

| Outcome path | Count | % |
|---|---|---|
| Target hit (WIN) | 20 | 17.4% |
| Stop hit (LOSS) | 23 | 20.0% |
| Time exit (final close price) | 72 | **62.6%** |

**62.6% of "verified" replay outcomes never actually hit the stop or target** — they
resolve on whatever the close price happened to be a few candles later. This is
intentional per the code's own design (a bounded lookahead window has to end
somewhere), but it means the majority of the historical-replay knowledge base is
built on a noisier signal (directional drift over N days) than the strategy itself
claims to trade (stop/target levels). Treat replay-derived confidence scores as
weaker evidence than a real stop/target resolution.

## Finding 2: ML Model Data Quality

**Status: CAUTION**

- `data/cache/forced_trades_history.json` + `intraday_sim_history.json` combined:
  **202 total labeled (WIN/LOSS) training samples**.
- **0/202 records carry an `indicators_snapshot` field.** The stored trade schema
  is `{closed_at, entry, exit, is_forced, market_was_closed, opened_at, outcome,
  pnl_pct, rationale, regime, signal_score, stop, symbol, target, tier,
  trade_type}` — no raw indicator values (RSI, ADX, CMF, etc.) are persisted per
  trade. Any feature-reconstruction that tries to rebuild indicator-level features
  from trade history alone has nothing to reconstruct from; it only has the single
  scalar `signal_score` plus categorical `tier`/`regime`.
- **Random Forest** (`src/ml/random_forest_model.py`): calling `.train()` directly
  succeeds on the 202 available samples (`val_accuracy 0.607`, above the 0.52
  deploy floor), but **feature importance is `score: 0.841`, `tier_mid: 0.109`,
  `tier_large: 0.05`, `rsi: 0.0`, `adx_value: 0.0`**. The model is not learning from
  raw technical indicators at all — it is almost entirely keying off the same
  pre-computed rule-based `score` that already went into generating the label. This
  is close to tautological: the RF isn't adding independent signal beyond what the
  rule-based scorer already encoded, which undercuts the premise of blending it
  into the judge's score (`src/judge/evaluator.py`) as if it were an independent
  ML opinion.
- No persisted RF model/importance file existed prior to this audit — a fresh
  `RandomForestModel()` returned empty `get_feature_importance()` until `.train()`
  was explicitly called. Confirm the scheduled `rf_train_job` (per `runner.py`) is
  actually running and persisting `data/models/random_forest.json` in practice.

## Finding 3: Fundamental Data Coverage

**Status: OK** (with one parsing bug)

Tested 15 stocks across large/mid/small tiers against live screener.in:

**13/15 (87%) returned real fundamental data.** `BANDHANBNK` and `CANFINHOME`
fell back to neutral (no HTML returned / parse produced no non-null numeric
fields) — both logged via `log.warning` per the existing screener.py behavior.
87% coverage is well above the 70% "good coverage" bar.

**Bug found**: `debt_to_equity` returned `None` for **all 13** stocks with
otherwise-real data, including large caps with well-known, publicly available D/E
figures (RELIANCE, TCS, HDFCBANK, INFY, ITC). This points to a label-text mismatch
in `ScreenerScraper._parse()`'s `label_map` (`"debt to equity"` likely doesn't match
current screener.in markup for the ratio). Practical consequence: the hard-reject
rule for "Dangerous debt levels (D/E > 3.0)" in `check_hard_rejects()` can
currently never fire, silently disabling one of five hard-reject checks.

## Finding 4: Stop-Loss Enforcement

**Status: OK, but unverified on the live path**

- **Real trades checked: 0.** The `TradingJournal` currently has zero real
  executed trades, so this audit could not verify stop-loss integrity on an
  actual pipeline execution — only on cached simulation/forced-trade JSON files.
- Scanned `forced_trades_history.json`, `intraday_sim_history.json`,
  `short_trades_history.json`: **0 stop-loss violations** — every record with a
  stop price has it below (or appropriately positioned relative to) entry.
- This is a genuinely clean result, but it's worth being honest that it has not
  yet been exercised on a real paper-broker fill — only on records the system
  wrote to its own simulation cache.

## Finding 5: Signal Stability

**Status: STABLE**

Ran `ScoutAgent().scan()` twice, 2 seconds apart. Top-5 candidates were **identical
both times** (`INDUSINDBK, METROPOLIS, AJANTPHARM, GRANULES, LICI` — 5/5 overlap).
No evidence of randomness or data-race instability in the scout scoring path.

## Finding 6: Kill Switch

**Status: WORKING**

Created `KILL_SWITCH` at repo root, ran `RiskChecker().check()` against a state
that would otherwise pass every gate (judge approved 9.0/10, valid stop/target,
BULL_TRENDING regime). Result: `approved=False`, reason `"KILL_SWITCH file present
— all execution halted."` File was removed immediately after the test. Confirmed
working exactly as CLAUDE.md rule 5 requires.

## Finding 7: Transaction Cost Model

**Status: STT CORRECT**

On a ₹2500 → ₹2575 (3.0% gross) delivery round-trip, 1 share:

| Component | Amount |
|---|---|
| Brokerage | ₹0.00 (zero-brokerage delivery, as expected for most Indian discount brokers) |
| STT | ₹5.08 |
| Exchange | ₹0.15 |
| Stamp duty | ₹0.37 |
| GST | ₹0.03 |
| Slippage | ₹2.54 |
| **Total** | **₹8.17 (0.327%)** |

STT computed exactly matches the naive "0.1% both legs on turnover" check
(₹5.08 vs ₹5.08 expected) — **STT calculation is correct**. Intraday round-trip
total came in slightly lower (0.269%) as expected (no delivery STT leg).
Breakeven move needed to clear costs: ~0.33% — well within normal swing-trade
target sizing, so costs are not inherently prohibitive; the concern is elsewhere
(see Finding 9).

## Finding 8: Regime Performance Concentration

**Status: CONCENTRATED**

| Regime | Trades | Win Rate | Avg P&L | % of all wins |
|---|---|---|---|---|
| VOLATILE | 128 | 52% | +0.1% | **67%** ⚠️ |
| RECOVERY | 74 | 45% | +0.5% | 33% |

67% of all wins in the accumulated learning history come from the VOLATILE
regime alone — above the 60% concentration flag. More notably, **only two
regimes appear in the sample at all** (VOLATILE, RECOVERY) — there is no
BULL_TRENDING, SIDEWAYS, or DOWNTREND trade history yet to validate performance
in those conditions. The system's learned confidence is currently backed by a
narrower slice of market conditions than the full regime taxonomy it reasons
about.

## Finding 9: Net Expectancy

**Status: NEGATIVE** ⚠️ (most important finding in this audit)

Across the 202 labeled trades in the simulation/forced-trade history:

| | Win Rate | Avg Win | Avg Loss | Expectancy/trade |
|---|---|---|---|---|
| GROSS (before costs) | 41.1% | +3.19% | -1.82% | **+0.238%** |
| NET (after Indian costs) | 30.2% | +3.81% | -1.95% | **-0.210%** |

Applying realistic round-trip transaction costs (`net_pnl_pct()`) flips the
system from gross-profitable to **net-loss-making**, and the win rate itself
drops from 41.1% to 30.2% — meaning a meaningful share of trades are small gross
wins that costs turn into net losses. **This is the strongest signal in this
audit against going live right now.** The strategy signal quality, as currently
calibrated, does not yet clear the real cost of trading in the Indian market.

## GO/NO-GO Criteria for Live Trading

- [ ] 30+ real pipeline paper trades completed — **0 real trades in journal**
- [ ] Net expectancy positive over those 30 trades — **currently -0.210%/trade net, on simulation data**
- [x] Kill switch working (VERIFIED)
- [x] Stop-loss on every trade (VERIFIED on cached simulation data; unverified on a real fill)
- [x] Stable signals (VERIFIED — 5/5 overlap)
- [ ] Performance not regime-concentrated — **67% of wins from VOLATILE regime alone**

**4 of 6 criteria unmet or unverified. Verdict: NOT YET.**

## Recommended Next Actions

1. **Do not enable live trading** until net expectancy over real (not simulated)
   paper trades turns positive after costs — the -0.210%/trade net figure is the
   central blocker.
2. Accumulate real paper trades through the full pipeline (not forced/simulated)
   until the journal has 30+, then rerun this audit's Finding 9 methodology
   against real trades specifically — simulation-derived expectancy may not
   transfer to live pipeline behavior.
3. Fix the `debt_to_equity` parsing gap in `ScreenerScraper._parse()` — the
   `label_map` entry for `"debt to equity"` needs to be re-checked against
   current screener.in markup; this hard-reject rule is currently dead code.
4. Investigate why the Random Forest model's feature importance is ~84%
   concentrated on the pre-computed `score` input with `rsi`/`adx_value` at 0.0
   — either engineer genuinely independent features into the RF, or stop
   presenting it as an independent ML opinion in the judge's blended score if it
   is just re-weighting the existing rule-based signal.
5. Persist `indicators_snapshot` (or equivalent raw indicator values) on every
   forced/intraday/short trade record going forward, so future feature-quality
   audits and ML retraining have real indicator-level data to work with instead
   of only `signal_score`/`tier`/`regime`.
6. Actively seek (or wait for) trades in BULL_TRENDING, SIDEWAYS, and DOWNTREND
   regimes — current learning evidence is concentrated in VOLATILE/RECOVERY only,
   so confidence outside those two regimes is unproven.
7. Re-run this audit after the above changes, specifically re-checking Finding 9
   (net expectancy) and Finding 8 (regime concentration) — those two are the
   actual gate on a future live-trading decision, not a formality.
