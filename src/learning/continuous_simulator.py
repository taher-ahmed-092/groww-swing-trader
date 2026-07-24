"""
Continuous Simulation Engine — runs 24/7, even when NSE is closed.

When market is open: augments live forced trades (src/trading/always_on_trader.py).
When market is closed: this is the only source of new learning data. Historical
data only (no look-ahead) — every simulated entry uses data up to window_end,
and outcomes are verified strictly from window_end+1 onward.

Key design principles:
- Multiple different historical windows per stock per batch (not just the most
  recent) — prevents the knowledge base from only ever learning today's pattern.
- Rotates through stocks least-recently simulated first, so all watchlist names
  get covered over time, not just the same handful.
- Statistical guard on the knowledge base: a new pattern starts at LOW confidence
  (0.03-0.05) regardless of its first outcome — a single win/loss is never
  treated as proof. Existing patterns update with a diminishing-weight nudge
  (more observations -> smaller confidence swing per new data point).
"""
from __future__ import annotations

import json
import logging
import random
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.agents.technical.indicators import compute_indicators
from src.data.fetcher import MarketDataFetcher
from src.data.watchlist import ALL_STOCKS
from src.memory.journal import KnowledgeEntry, TradingJournal
from src.utils.serialization import sanitize_for_state

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
SIM_PROGRESS_FILE = Path("data/cache/continuous_sim_progress.json")
SIM_HISTORY_FILE = Path("data/cache/continuous_sim_history.json")
HISTORY_RETENTION = 10000


class ContinuousSimulator:
    """Runs unlimited paper simulations on historical data. Stateless per call —
    safe to invoke as often as desired (progress/history persist to JSON)."""

    BATCH_SIZE = 8       # stocks per batch
    DAYS_PER_STOCK = 3   # different historical windows per stock
    MIN_WINDOW_GAP = 15  # jitter bound between window endpoints (candles)

    def __init__(self) -> None:
        self.fetcher = MarketDataFetcher()
        self.journal = TradingJournal()

    def run_batch(self) -> dict:
        """Runs one batch of simulations across BATCH_SIZE stocks, each on
        DAYS_PER_STOCK different historical windows.
        Returns: {simulated, wins, losses, new_patterns, batch_symbols}."""
        progress = self._load_progress()

        all_syms = list(ALL_STOCKS.keys())
        by_last_sim = sorted(
            all_syms,
            key=lambda s: progress.get(s, {}).get("last_sim", "2000-01-01"),
        )
        batch = by_last_sim[: self.BATCH_SIZE]

        wins = losses = new_patterns = 0
        sim_records: list[dict] = []

        for symbol in batch:
            tier = ALL_STOCKS[symbol].get("tier", "large")
            try:
                df = self.fetcher.get_price_history(symbol, period="6mo")
                if df is None or len(df) < 60:
                    continue

                windows = self._pick_windows(len(df), self.DAYS_PER_STOCK)
                for window_end in windows:
                    result = self._simulate_window(symbol, tier, df, window_end)
                    if result:
                        sim_records.append(result)
                        if result["outcome"] == "WIN":
                            wins += 1
                        else:
                            losses += 1
                        if result.get("pattern_added"):
                            new_patterns += 1

                progress[symbol] = {
                    "last_sim": date.today().isoformat(),
                    "total_sims": (progress.get(symbol, {}).get("total_sims", 0)
                                  + self.DAYS_PER_STOCK),
                }
                time.sleep(0.3)  # respectful to yfinance
            except Exception as exc:
                log.debug("Continuous sim failed for %s: %s", symbol, exc)

        self._save_progress(progress)
        self._append_history(sim_records)

        return {
            "simulated": len(sim_records),
            "wins": wins,
            "losses": losses,
            "new_patterns": new_patterns,
            "batch_symbols": batch,
        }

    def _pick_windows(self, df_len: int, n: int) -> list[int]:
        """Picks n different historical window endpoints, evenly spaced across
        the available history (+ small jitter) so different market conditions
        get exercised rather than always the same recent slice."""
        max_idx = df_len - 10   # reserve the tail for outcome verification
        min_idx = 50             # need enough history for indicators (MA50 etc.)

        if max_idx <= min_idx:
            return [max_idx] if max_idx > 0 else []

        step = max(1, (max_idx - min_idx) // max(n, 1))
        windows = []
        for i in range(n):
            base = min_idx + i * step
            jitter = random.randint(-self.MIN_WINDOW_GAP // 2, self.MIN_WINDOW_GAP // 2)
            idx = max(min_idx, min(max_idx, base + jitter))
            windows.append(idx)
        return sorted(set(windows))

    def _simulate_window(self, symbol: str, tier: str, df: pd.DataFrame,
                         window_end: int) -> dict | None:
        """Simulates one trade at window_end using only data up to window_end
        (no look-ahead); verifies the outcome from window_end+1 onward."""
        df_at = df.iloc[: window_end + 1].copy()
        df_after = df.iloc[window_end + 1: window_end + 11]
        if len(df_after) < 3:
            return None

        indicators = compute_indicators(df_at)
        if not indicators:
            return None
        indicators = sanitize_for_state(indicators)

        entry = float(df_at["Close"].iloc[-1])
        if entry <= 0:
            return None

        signal = self._evaluate_signal(indicators, tier, entry)
        if signal["action"] == "SKIP":
            return None

        atr = indicators.get("atr_14") or (entry * 0.02)
        stop = round(max(entry * 0.92, entry - 2 * atr), 2)
        target = round(entry + 3 * (entry - stop), 2)  # 3:1 R:R

        outcome, exit_price, days = self._check_outcome(entry, stop, target, df_after)
        pnl_pct = round((exit_price - entry) / entry * 100, 2)

        ma50_series = df_at["Close"].rolling(50).mean()
        ma50 = float(ma50_series.iloc[-1]) if len(df_at) >= 50 else None
        regime = "UPTREND" if ma50 and entry > ma50 else "DOWNTREND"

        pattern_added = self._update_knowledge_safely(
            symbol, tier, indicators, signal, outcome, regime)

        return {
            "symbol": symbol, "tier": tier, "window_end": window_end,
            "entry": entry, "stop": stop, "target": target, "exit": exit_price,
            "pnl_pct": pnl_pct, "outcome": outcome, "days_held": days,
            "regime": regime, "signal_score": signal["score"],
            "rsi": indicators.get("rsi_14", 50), "trend": indicators.get("trend", "SIDEWAYS"),
            "adx": indicators.get("adx_signal", "NEUTRAL"),
            "strategy_type": signal.get("strategy_type", "momentum"),
            "simulated_at": datetime.now(IST).isoformat(), "source": "continuous_sim",
            "pattern_added": pattern_added,
        }

    def _evaluate_signal(self, ind: dict, tier: str, entry: float) -> dict:
        """Fast rule-based signal — no LLM needed, pure math over indicators.

        Distinguishes two signal types so the knowledge base can build
        separate patterns for each: momentum (uptrend + volume confirmation)
        and mean reversion (deeply oversold, but still above the long-term
        trend). Audit finding: DOWNTREND wins in this system's history came
        from oversold bounces, not momentum continuation — a plain
        `rsi < 35` boost regardless of the long-term trend would just as
        happily fire on a stock in genuine structural decline, so it's gated
        on price still being above MA200 (unknown MA200 = insufficient
        history = don't grant the extra boost, stay conservative)."""
        rsi = ind.get("rsi_14", 50) or 50
        trend = ind.get("trend", "SIDEWAYS") or "SIDEWAYS"
        adx = ind.get("adx_signal", "NEUTRAL") or "NEUTRAL"
        obv = ind.get("obv_trend", "NEUTRAL") or "NEUTRAL"
        supertrend = ind.get("supertrend_direction", "NEUTRAL") or "NEUTRAL"

        score = 0.0
        strategy_type = "momentum"
        if 48 <= rsi <= 68:
            score += 0.30
        elif rsi < 35:
            score += 0.15  # baseline oversold bounce potential
            ma200 = ind.get("ma_200")
            if ma200 is not None and entry > ma200:
                score += 0.25  # deeply oversold but still above the long-term trend
                strategy_type = "mean_reversion"
        elif rsi > 72:
            score -= 0.25
        if trend == "UPTREND":
            score += 0.25
        elif trend == "DOWNTREND":
            score -= 0.20
        if adx == "TRENDING":
            score += 0.20
        elif adx == "CHOPPY":
            score -= 0.10
        if obv == "RISING":
            score += 0.15
        if supertrend == "BULLISH":
            score += 0.10

        thresholds = {"large": 0.30, "mid": 0.35, "small": 0.40}
        threshold = thresholds.get(tier, 0.35)
        action = "BUY" if score >= threshold else "SKIP"
        return {"action": action, "score": round(score, 3), "strategy_type": strategy_type}

    @staticmethod
    def _check_outcome(entry: float, stop: float, target: float,
                       df_after: pd.DataFrame) -> tuple[str, float, int]:
        """Stop-vs-target first (no look-ahead beyond window); time exit last."""
        for i, (_, row) in enumerate(df_after.iterrows()):
            if row["Low"] <= stop:
                return "LOSS", stop, i + 1
            if row["High"] >= target:
                return "WIN", target, i + 1
        final = float(df_after["Close"].iloc[-1])
        outcome = "WIN" if final > entry else "LOSS"
        return outcome, final, len(df_after)

    def _update_knowledge_safely(self, symbol: str, tier: str, indicators: dict,
                                 signal: dict, outcome: str, regime: str) -> bool:
        """Updates the knowledge base with a statistical guard: a brand-new
        pattern starts at LOW confidence (0.03-0.05) — a single data point is
        never treated as proof. Existing patterns nudge via the journal's own
        update_knowledge_confidence (which already applies a fixed +0.1/-0.05
        step, itself bounded and never single-point-decisive)."""
        rsi = indicators.get("rsi_14", 50) or 50
        trend = indicators.get("trend", "SIDEWAYS") or "SIDEWAYS"
        adx = indicators.get("adx_signal", "NEUTRAL") or "NEUTRAL"
        rsi_bucket = "high" if rsi > 60 else "mid" if rsi > 45 else "low"
        pattern_id = f"contsim-{tier}-{trend.lower()[:2]}-rsi{rsi_bucket}-adx{adx.lower()[:4]}"

        try:
            existing_kb = self.journal.get_active_knowledge(min_confidence=0.0)
            existing = next((e for e in existing_kb if e.pattern_id == pattern_id), None)

            if existing:
                self.journal.update_knowledge_confidence(pattern_id, confirmed=(outcome == "WIN"))
                return False

            initial_conf = 0.05 if outcome == "WIN" else 0.03
            desc = (f"CONT-SIM {'WIN' if outcome == 'WIN' else 'LOSS'}: "
                    f"{tier} | RSI {rsi_bucket} | {trend} | ADX {adx} | score {signal['score']:.2f}")
            self.journal.log_knowledge_entry(KnowledgeEntry(
                pattern_id=pattern_id,
                pattern_description=desc,
                category="CONTINUOUS_SIM",
                confidence=initial_conf,
                observed_in_regime=regime,
                observed_count=1,
                is_hypothesis=True,
                first_seen=datetime.now(IST),
                last_confirmed=datetime.now(IST),
                last_seen_in_trade=symbol,
                supporting_trades=json.dumps([f"{symbol}_{date.today()}"]),
            ))
            return True
        except Exception:
            return False

    def get_stats(self) -> dict:
        history = self._load_history()
        progress = self._load_progress()
        wins = sum(1 for t in history if t.get("outcome") == "WIN")
        total = len(history)
        return {
            "total": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "stocks_covered": len(progress),
            "recent": history[-5:],
        }

    # ── persistence ──────────────────────────────────────────────────────────
    def _load_progress(self) -> dict:
        if SIM_PROGRESS_FILE.exists():
            try:
                return json.loads(SIM_PROGRESS_FILE.read_text())
            except Exception:
                pass
        return {}

    def _save_progress(self, progress: dict) -> None:
        try:
            SIM_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
            SIM_PROGRESS_FILE.write_text(json.dumps(progress))
        except OSError:
            pass

    def _load_history(self) -> list:
        if SIM_HISTORY_FILE.exists():
            try:
                return json.loads(SIM_HISTORY_FILE.read_text())
            except Exception:
                pass
        return []

    def _append_history(self, new_records: list) -> None:
        if not new_records:
            return
        history = self._load_history()
        history.extend(new_records)
        try:
            SIM_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            SIM_HISTORY_FILE.write_text(json.dumps(history[-HISTORY_RETENTION:]))
        except OSError:
            pass
