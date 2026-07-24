"""
Historical Replay Engine — the system's night school.

When markets are closed (and lightly during the day), this engine replays past
trading days through the analysis pipeline. For each replay:
  1. Fetches OHLCV data UP TO that date (no future data — no cheating)
  2. Runs the deterministic indicator + signal evaluation
  3. Records what the system WOULD have decided
  4. Checks what ACTUALLY happened in the days after
  5. Logs the verified outcome to the knowledge base

Genuine learning from verified evidence — no LLM calls, so it runs fast and free.
244 stocks × 60 trading days ≈ 14,640 learning scenarios. Progress is cached so
replays pick up where they left off across restarts, rotating through every stock.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config.risk_limits import LIMITS
from src.agents.technical.indicators import compute_indicators
from src.data.fetcher import MarketDataFetcher
from src.data.watchlist import ALL_STOCKS
from src.memory.journal import KnowledgeEntry, TradingJournal

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
REPLAY_CACHE = Path("data/cache/replay_progress.json")


class HistoricalReplayEngine:
    """Replays historical trading days to generate verified learning data.
    Safe (only data up to the replay date), fast (pure math), continuous."""

    REPLAY_LOOKBACK_DAYS = 60   # how far back replay days are drawn from
    BATCH_SIZE = 15             # stocks per batch (bounds yfinance calls)
    MIN_REPLAY_INTERVAL_DAYS = 7

    def __init__(self) -> None:
        self.journal = TradingJournal()
        self.fetcher = MarketDataFetcher()
        # Session-scoped dedup: pattern_id is derived from tier/trend/RSI-bucket/
        # ADX-signal, NOT the specific stock — so replaying many days of the same
        # stock (or several stocks sharing a regime bucket) in one run_batch()
        # call could hit the identical pattern repeatedly and inflate its
        # confidence well past what the evidence actually supports. One update
        # per pattern per calendar day, per engine instance (= per batch run).
        self._updated_today: set[str] = set()

    # ── batch driver ─────────────────────────────────────────────────────────
    def run_batch(self, n_stocks: int = 15, n_days_each: int = 5) -> dict:
        """Replay n_stocks across n_days_each historical days each. ~2-5 min."""
        stocks = self._pick_stocks_to_replay(n_stocks)
        if not stocks:
            return {"replayed": 0, "signals_found": 0, "wins": 0,
                    "losses": 0, "patterns_added": 0}

        signals_found = wins = losses = patterns_added = 0
        for symbol in stocks:
            tier = ALL_STOCKS.get(symbol, {}).get("tier", "large")
            try:
                r = self._replay_stock(symbol, tier, n_days_each)
                signals_found += r["signals_found"]
                wins += r["wins"]
                losses += r["losses"]
                patterns_added += r["patterns_added"]
                time.sleep(0.5)  # be gentle with yfinance
            except Exception as exc:  # noqa: BLE001 — one bad symbol never sinks the batch
                log.debug("Replay failed for %s: %s", symbol, exc)

        self._update_progress(stocks)
        return {"replayed": len(stocks), "signals_found": signals_found,
                "wins": wins, "losses": losses, "patterns_added": patterns_added}

    # ── per-stock replay ─────────────────────────────────────────────────────
    def _replay_stock(self, symbol: str, tier: str, n_days: int) -> dict:
        zeros = {"signals_found": 0, "wins": 0, "losses": 0, "patterns_added": 0}
        df_full = self.fetcher.get_price_history(symbol, period="6mo")
        if df_full is None or len(df_full) < 60:
            return zeros

        signals_found = wins = losses = patterns_added = 0

        # Walk recent days (every 3rd) leaving 5 days at the end for outcome checks.
        replay_indices = list(range(len(df_full) - n_days * 3, len(df_full) - 5, 3))
        replay_indices = [i for i in replay_indices if i >= 50]

        for idx in replay_indices[-n_days:]:
            df_at_date = df_full.iloc[: idx + 1].copy()  # no future data
            replay_date = df_full.index[idx]
            df_after = df_full.iloc[idx + 1: idx + 6]     # next 5 candles
            if len(df_after) < 3:
                continue

            indicators = compute_indicators(df_at_date)
            if indicators is None:
                continue

            entry_price = float(df_at_date["Close"].iloc[-1])
            stop_price = round(entry_price * (1 - LIMITS.stop_loss_pct / 100), 2)
            target_price = round(entry_price + (entry_price - stop_price) * 2.0, 2)

            signal = self._evaluate_signal(indicators, tier)
            if signal["action"] != "BUY":
                continue
            signals_found += 1

            outcome, exit_price, _days = self._check_actual_outcome(
                entry_price, stop_price, target_price,
                df_after["High"].tolist(), df_after["Low"].tolist(),
                df_after["Close"].tolist())
            if outcome == "WIN":
                wins += 1
            else:
                losses += 1
            pnl_pct = round((exit_price - entry_price) / entry_price * 100, 2)

            desc = self._build_pattern_description(symbol, indicators, signal, outcome, tier)
            date_str = (replay_date.strftime("%Y-%m-%d")
                        if hasattr(replay_date, "strftime") else str(replay_date))
            if self._update_knowledge(symbol, tier, indicators, signal,
                                      outcome, pnl_pct, desc, date_str):
                patterns_added += 1

        return {"signals_found": signals_found, "wins": wins,
                "losses": losses, "patterns_added": patterns_added}

    # ── signal evaluation (fast, no LLM) ─────────────────────────────────────
    @staticmethod
    def _evaluate_signal(indicators: dict, tier: str) -> dict:
        rsi = indicators.get("rsi_14", 50) or 50
        trend = indicators.get("trend", "SIDEWAYS") or "SIDEWAYS"
        adx_signal = indicators.get("adx_signal", "NEUTRAL") or "NEUTRAL"
        obv_trend = indicators.get("obv_trend", "NEUTRAL") or "NEUTRAL"
        supertrend = indicators.get("supertrend_direction", "NEUTRAL") or "NEUTRAL"
        cmf = indicators.get("cmf_20", 0) or 0

        score = 0.0
        if 50 <= rsi <= 65:
            score += 0.25
        elif 45 <= rsi < 50:
            score += 0.10
        elif rsi < 35:
            score += 0.15  # oversold bounce potential
        elif rsi > 72:
            score -= 0.20  # overbought

        if trend == "UPTREND":
            score += 0.25
        elif trend == "DOWNTREND":
            score -= 0.30

        if adx_signal == "TRENDING":
            score += 0.15
        elif adx_signal == "CHOPPY":
            score -= 0.10

        if obv_trend == "RISING":
            score += 0.15

        if supertrend == "BULLISH":
            score += 0.15
        elif supertrend == "BEARISH":
            score -= 0.15

        if cmf > 0.1:
            score += 0.10
        elif cmf < -0.1:
            score -= 0.10

        threshold = {"large": 0.35, "mid": 0.40, "small": 0.45}.get(tier, 0.40)
        if score >= threshold:
            action = "BUY"
        elif score <= -0.20:
            action = "AVOID"
        else:
            action = "SKIP"
        return {"action": action, "score": round(score, 3)}

    @staticmethod
    def _check_actual_outcome(entry: float, stop: float, target: float,
                              future_highs: list, future_lows: list,
                              future_closes: list) -> tuple[str, float, int]:
        """What ACTUALLY happened after a hypothetical entry — stop vs target first."""
        for i, (high, low, _close) in enumerate(
                zip(future_highs, future_lows, future_closes)):
            if low <= stop:
                return "LOSS", stop, i + 1
            if high >= target:
                return "WIN", target, i + 1
        final = future_closes[-1]
        return ("WIN" if final > entry else "LOSS"), final, len(future_closes)

    @staticmethod
    def _build_pattern_description(symbol: str, indicators: dict, signal: dict,
                                   outcome: str, tier: str) -> str:
        rsi = indicators.get("rsi_14", 50) or 50
        trend = indicators.get("trend", "SIDEWAYS") or "SIDEWAYS"
        adx = indicators.get("adx_signal", "NEUTRAL") or "NEUTRAL"
        result = "✅ WON" if outcome == "WIN" else "❌ LOST"
        return (f"REPLAY {result}: {symbol} ({tier}) | RSI {rsi:.0f} | "
                f"{trend} | ADX {adx} | Score {signal['score']:.2f}")

    def _update_knowledge(self, symbol: str, tier: str, indicators: dict,
                          signal: dict, outcome: str, pnl_pct: float,
                          description: str, date_str: str) -> bool:
        """Confirm/seed a knowledge pattern from a verified replay outcome."""
        rsi = indicators.get("rsi_14", 50) or 50
        trend = indicators.get("trend", "SIDEWAYS") or "SIDEWAYS"
        adx = indicators.get("adx_signal", "NEUTRAL") or "NEUTRAL"
        rsi_bucket = "high" if rsi > 60 else "mid" if rsi > 45 else "low"
        pattern_id = f"replay-{tier}-{trend.lower()}-rsi{rsi_bucket}-adx{adx.lower()}"

        cache_key = f"{pattern_id}_{date.today()}"
        if cache_key in self._updated_today:
            return False  # already updated this pattern today — don't inflate it further
        self._updated_today.add(cache_key)

        try:
            existing = [e for e in self.journal.get_active_knowledge(min_confidence=0.0)
                        if e.pattern_id == pattern_id]
            if existing:
                self.journal.update_knowledge_confidence(pattern_id, confirmed=(outcome == "WIN"))
                return False
            self.journal.log_knowledge_entry(KnowledgeEntry(
                pattern_id=pattern_id,
                pattern_description=description,
                category="HISTORICAL_REPLAY",
                confidence=0.08 if outcome == "WIN" else 0.05,
                observed_in_regime=trend,
                observed_count=1,
                is_hypothesis=True,
                first_seen=datetime.now(IST),
                last_confirmed=datetime.now(IST),
                last_seen_in_trade=symbol,
                supporting_trades=json.dumps([f"{symbol}_{date_str}"]),
            ))
            return True
        except Exception:  # noqa: BLE001 — never let a KB write crash a replay
            return False

    # ── rotation / progress ──────────────────────────────────────────────────
    def _pick_stocks_to_replay(self, n: int) -> list[str]:
        """Most-overdue stocks first, so every name is replayed regularly."""
        progress = self._load_progress()

        def overdue_days(sym: str) -> int:
            last = progress.get(sym, "2020-01-01")
            try:
                return (date.today() - date.fromisoformat(last)).days
            except Exception:
                return 999

        return sorted(ALL_STOCKS.keys(), key=overdue_days, reverse=True)[:n]

    def _load_progress(self) -> dict:
        if REPLAY_CACHE.exists():
            try:
                return json.loads(REPLAY_CACHE.read_text())
            except Exception:
                pass
        return {}

    def _update_progress(self, replayed: list[str]) -> None:
        progress = self._load_progress()
        today = date.today().isoformat()
        for sym in replayed:
            progress[sym] = today
        try:
            REPLAY_CACHE.parent.mkdir(parents=True, exist_ok=True)
            REPLAY_CACHE.write_text(json.dumps(progress))
        except OSError:
            pass

    def get_stats(self) -> dict:
        progress = self._load_progress()
        replay_patterns = [e for e in self.journal.get_active_knowledge(min_confidence=0.0)
                           if e.category == "HISTORICAL_REPLAY"]
        return {
            "total_stocks_replayed": len(progress),
            "replay_patterns_in_kb": len(replay_patterns),
            "stocks_pending": max(0, len(ALL_STOCKS) - len(progress)),
        }
