"""
Always-On Paper Trading Engine — the learning-volume engine.

Places at least 3 FORCED learning trades every single day, no exceptions:
- Market open  + good signal : best available stock, full indicator scoring
- Market open  + bad signal  : forced entry on best-of-sample anyway
- Market open  + no signal   : random large-cap (learning from chaos)
- Market closed / weekend     : simulate on recent historical data, outcome verified

Stop-loss is ALWAYS applied. The goal is maximum learning data, not profit.

IMPORTANT — these are NOT pipeline trades. They never touch the broker, the judge,
or the TradeRecord journal (CLAUDE.md rules 3 & 8 keep that path judge+risk gated).
They are SIMULATIONS: persisted to JSON (open + history), mirrored into the
SimulatedTrade store (tagged FORCED_*), and used to grow the knowledge base via
outcome analysis. Real signal-quality metrics (win rate, Kelly, XGBoost) are
computed only from real TradeRecord trades and are untouched by anything here.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from src.agents.technical.indicators import compute_indicators
from src.data.fetcher import MarketDataFetcher
from src.data.watchlist import ALL_STOCKS, LARGE_CAP
from src.memory.journal import KnowledgeEntry, TradingJournal
from src.trading.cost_model import compute_round_trip_costs, net_pnl_pct

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# Forced-trade universe: every stock in the watchlist, for maximum learning diversity.
FORCED_TRADE_UNIVERSE = list(ALL_STOCKS.keys())

FORCED_TRADE_FILE = Path("data/cache/forced_trades_today.json")
FORCED_HISTORY_FILE = Path("data/cache/forced_trades_history.json")
# Audit finding: this was capped at 200, and the forced-trade engine places
# enough volume (~200/day) to fully cycle the cap within a single day — so ML
# training only ever sees a few hours of history, not weeks of it. Raised so
# accumulated volume actually grows the training set instead of perpetually
# discarding everything older than "today."
HISTORY_RETENTION = 3000


class AlwaysOnTrader:
    TARGET_DAILY_TRADES = 999  # effectively unlimited
    # Audit finding: 273 forced trades in one day was churn, not learning —
    # and combined with a 90-min exit vs 6%+ targets, it inverted the realized
    # R:R (winners = small time-exits, losers = full stops + costs, PF 0.53).
    # Multi-day holds (see close_open_forced_trades) need volume capped so a
    # position actually gets time to reach its target instead of being one of
    # hundreds churned through in a single session.
    MAX_DAILY_TRADES = 30      # quality over churn
    MAX_OPEN_FORCED = 15       # concurrent open forced positions
    MAX_HOLD_DAYS = 5          # multi-day hold, not 90 minutes

    def __init__(self) -> None:
        self.journal = TradingJournal()
        self.fetcher = MarketDataFetcher()

    # ── public API ───────────────────────────────────────────────────────────
    def ensure_daily_trades(self) -> list[dict]:
        """Place forced trades until today's count reaches the daily target.
        Up to 3 per call. Market hours → live forced; otherwise → historical sim."""
        from src.risk.checker import is_daily_loss_halted

        if is_daily_loss_halted():
            return []
        placed_today = self._count_todays_forced_trades()
        if placed_today >= self.MAX_DAILY_TRADES:
            return []
        if len(self._load_open_forced_trades()) >= self.MAX_OPEN_FORCED:
            return []  # let existing positions actually play out before opening more

        is_market_hours = self._is_market_hours(datetime.now(IST))

        # HISTORICAL_SIM is retired (scorecard evidence: PF 0.31 pre-fix era,
        # PF 0.21 post-fix, 62.6% noisy time-exits) — off-hours learning is
        # ContinuousSimulator's job now (it already runs every 30 min,
        # 24/7, on the same historical data with a proper 3:1 R:R). Its
        # history file is kept for learning; no new HISTORICAL_SIM trades
        # are placed off-hours.
        if not is_market_hours:
            return []

        # Meta-learning: an engine that's been consistently unprofitable over
        # real evidence gets its own entries throttled or paused — the system
        # adjusting its own behavior, not just pattern confidences.
        from src.analytics.strategy_scorecard import EngineScorecard

        engine_name = "LIVE_FORCED"
        engine_mode = EngineScorecard.get_mode(engine_name)
        if engine_mode == "paused":
            return []
        # Cap per-call volume to avoid rate limits; this gets called every 15 min
        # throughout the day. "probation" (still building current-era evidence
        # post-fix) gets the same half-size treatment as "throttled" — allowed
        # to keep proving itself, just not at full volume.
        needed = 5
        if engine_mode in ("throttled", "probation"):
            needed = max(1, needed // 2)

        regime_name = self._current_regime()
        trades: list[dict] = []
        for _ in range(needed):
            trade = self._place_live_forced_trade()
            if not trade:
                continue
            trade.setdefault("regime", regime_name)
            trade.setdefault("market_regime", regime_name)
            trades.append(trade)
            self._save_forced_trade(trade)
            # Historical sims close immediately → learn now. Live forced trades are
            # OPEN and learn later when close_open_forced_trades() runs.
            if trade.get("outcome") not in (None, "OPEN"):
                self._update_knowledge_from_trade(trade)
                self._log_simulation_row(trade)
        return trades

    def close_open_forced_trades(self) -> list[dict]:
        """Multi-day positions: close on stop/target hit (checked every call,
        any day) or after MAX_HOLD_DAYS days, whichever comes first. The old
        90-minute exit was the root cause of an inverted realized R:R (audit
        finding: PF 0.53 despite 40% WR) — winners were small 90-min time-exits
        while losers ran the full stop distance plus costs, against 6%+
        targets that a 90-minute window could rarely reach. Positions persist
        across days in FORCED_TRADE_FILE; datetime.fromisoformat() on the
        stored IST-aware ISO string parses correctly across day boundaries."""
        open_trades = self._load_open_forced_trades()
        if not open_trades:
            return []

        now = datetime.now(IST)
        closed, still_open = [], []
        for trade in open_trades:
            try:
                opened_at = datetime.fromisoformat(trade["opened_at"])
                elapsed_days = (now.date() - opened_at.date()).days
            except Exception:
                elapsed_days = self.MAX_HOLD_DAYS  # malformed timestamp — force a close

            current = self._current_price(trade["symbol"])
            if current is None:
                still_open.append(trade)  # can't price it — leave open, try again next call
                continue

            hit_stop = current <= trade["stop"]
            hit_target = current >= trade["target"]
            time_exit = elapsed_days >= self.MAX_HOLD_DAYS
            if not (hit_stop or hit_target or time_exit):
                still_open.append(trade)
                continue

            result = self._close_forced_trade(trade, current=current)
            if result:
                closed.append(result)
                self._update_knowledge_from_trade(result)
                self._log_simulation_row(result)
            else:
                still_open.append(trade)

        self._write_json(FORCED_TRADE_FILE, still_open)
        if closed:
            history = self._load_history()
            history.extend(closed)
            self._write_json(FORCED_HISTORY_FILE, history[-HISTORY_RETENTION:])
        return closed

    def get_todays_summary(self) -> dict:
        """Closed-today counts (from history) plus opened-today and
        currently-open counts (from the still-open file) — multi-day holds
        stay OPEN for up to MAX_HOLD_DAYS, so a closed-only count reads 0
        for most of the day despite dozens of positions having been opened."""
        history = self._load_history()
        open_trades = self._load_open_forced_trades()
        today = datetime.now(IST).date().isoformat()
        todays_closed = [t for t in history if t.get("opened_at", "")[:10] == today]
        todays_open = [t for t in open_trades if t.get("opened_at", "")[:10] == today]
        wins = sum(1 for t in todays_closed if t.get("outcome") == "WIN")
        total = len(todays_closed)
        return {
            "total": total, "wins": wins, "losses": total - wins,
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "recent": todays_closed[-5:],
            "opened_today": len(todays_open) + total,
            "closed_today": total,
            "currently_open": len(open_trades),
        }

    # ── live forced trade (market open) ──────────────────────────────────────
    def _place_live_forced_trade(self) -> Optional[dict]:
        candidates = self._score_all_candidates_live()
        if not candidates:
            # Absolute fallback — random large-cap, trade from chaos.
            symbol = random.choice(list(LARGE_CAP.keys())[:20])
            df = self.fetcher.get_price_history(symbol)
            if df is None or len(df) < 20:
                return None
            entry = float(df["Close"].iloc[-1])
            return self._build_trade_record(
                symbol=symbol, entry=entry, indicators={}, signal_score=0.0,
                rationale="Random forced entry — learning from chaos",
                trade_type="RANDOM_FORCED")
        chosen = random.choice(candidates[:5])  # top-5 for variety
        return self._build_trade_record(**chosen)

    def _confirming_signals(self, ind: dict, regime: str) -> int:
        """Counts confirmations among {supertrend bullish, OBV rising, above VWAP}.
        Returns the count so callers can require 2-of-3 normally, or all 3 in a
        regime the adaptive thresholds have flagged as historically bad (WR<35%)."""
        supertrend_bull = ind.get("supertrend_direction") == "BULLISH"
        obv_rising = ind.get("obv_trend") == "RISING"
        above_vwap = ind.get("price_vs_vwap") == "ABOVE"
        return sum([supertrend_bull, obv_rising, above_vwap])

    def _passes_entry_filter(self, ind: dict, regime: str) -> bool:
        confirming = self._confirming_signals(ind, regime)
        if confirming < 2:
            return False
        try:
            from src.memory.adaptive_thresholds import AdaptiveThresholds

            regime_data = AdaptiveThresholds().load().get(regime, {})
            regime_wr = regime_data.get("win_rate", 0.5)
        except Exception:
            regime_wr = 0.5
        if regime_wr < 0.35 and confirming < 3:
            return False
        return True

    def _score_all_candidates_live(self) -> list[dict]:
        sample = random.sample(FORCED_TRADE_UNIVERSE, min(40, len(FORCED_TRADE_UNIVERSE)))
        regime = self._current_regime()
        results = []
        for symbol in sample:
            try:
                df = self.fetcher.get_price_history(symbol)
                if df is None or len(df) < 30:
                    continue
                entry = float(df["Close"].iloc[-1])
                if entry <= 0:
                    continue
                ind = compute_indicators(df)
                if not self._passes_entry_filter(ind, regime):
                    continue
                score = self._quick_score(ind)
                tier = ALL_STOCKS.get(symbol, {}).get("tier", "large")
                results.append({
                    "symbol": symbol, "entry": entry, "indicators": ind,
                    "signal_score": score,
                    "rationale": self._build_rationale(symbol, ind, score, tier),
                    "trade_type": "LIVE_FORCED",
                })
            except Exception:
                continue
        return sorted(results, key=lambda x: -x["signal_score"])

    # ── historical simulation (market closed) ────────────────────────────────
    def _place_historical_simulation_trade(self) -> Optional[dict]:
        symbol = random.choice(
            random.sample(FORCED_TRADE_UNIVERSE, min(20, len(FORCED_TRADE_UNIVERSE))))
        try:
            df = self.fetcher.get_price_history(symbol, period="60d")
            if df is None or len(df) < 21:
                return None
            # Leave 10 future candles to let the signal play out before recording
            # an outcome — checking only 1 candle (the old len(df)-2 entry point)
            # made outcomes close to random.
            entry_idx = len(df) - 11
            if entry_idx < 10:
                return None
            df_at_entry = df.iloc[: entry_idx + 1]
            df_after = df.iloc[entry_idx + 1: entry_idx + 11]

            entry = float(df_at_entry["Close"].iloc[-1])
            ind = compute_indicators(df_at_entry)
            regime = self._current_regime()
            if not self._passes_entry_filter(ind, regime):
                return None
            score = self._quick_score(ind)
            atr = ind.get("atr_14") or (entry * 0.02)
            tier = ALL_STOCKS.get(symbol, {}).get("tier", "large")
            stop, target, meets_min_move = self._stop_target_for_costs(entry, atr, tier)
            if not meets_min_move:
                return None

            outcome, exit_price = "LOSS", entry
            if len(df_after) > 0:
                fhigh = float(df_after["High"].max())
                flow = float(df_after["Low"].min())
                fclose = float(df_after["Close"].iloc[-1])
                if flow <= stop:
                    outcome, exit_price = "LOSS", stop
                elif fhigh >= target:
                    outcome, exit_price = "WIN", target
                else:
                    outcome = "WIN" if fclose > entry else "LOSS"
                    exit_price = fclose
            gross_pnl_pct = round((exit_price - entry) / entry * 100, 2) if entry else 0.0
            pnl_pct = net_pnl_pct(gross_pnl_pct, entry, exit_price, tier, is_intraday=True)

            now_iso = datetime.now(IST).isoformat()
            return {
                "symbol": symbol, "entry": entry, "stop": stop, "target": target,
                "exit": exit_price, "pnl_pct": pnl_pct, "gross_pnl_pct": gross_pnl_pct,
                "outcome": outcome,
                "signal_score": score, "opened_at": now_iso, "closed_at": now_iso,
                "trade_type": "HISTORICAL_SIM",
                "rationale": (f"Historical sim: {symbol} RSI {(ind.get('rsi_14') or 50):.0f} "
                              f"→ {outcome} ({pnl_pct:+.1f}%)"),
                "is_forced": True, "market_was_closed": True,
                "tier": ALL_STOCKS.get(symbol, {}).get("tier", "large"),
            }
        except Exception:
            return None

    # ── stop/target sizing (cost-aware) ──────────────────────────────────────
    @staticmethod
    def _stop_target_for_costs(entry: float, atr: float, tier: str) -> tuple[float, float, bool]:
        """ATR-based stop (capped at 7%), but the target is sized off real
        transaction costs rather than a flat 2:1 R:R. Audit finding: net
        expectancy was -0.21%/trade despite +0.24% gross — a 2:1 R:R on a tiny
        ATR stop produces targets that look fine on paper but get eaten by
        STT/slippage/GST once most trades exit on time (not target) at a small
        gross gain. Target = max(6%, 8x round-trip costs) so even a partial hit
        rate on target vs time-exit still clears costs with margin. Also reports
        whether this stock's own recent volatility (ATR) is even large enough to
        plausibly reach a cost-clearing move — trades on dead-quiet stocks get
        skipped rather than forced.
        """
        stop = max(round(entry - 1.5 * atr, 2), round(entry * 0.93, 2))  # <=7% stop
        costs = compute_round_trip_costs(entry, entry * 1.06, 1, tier, is_intraday=True)
        min_target_pct = max(6.0, costs.total_pct * 8)
        target = round(entry * (1 + min_target_pct / 100), 2)
        min_move_needed_pct = costs.total_pct * 3
        recent_atr_pct = (atr / entry * 100) if entry else 0.0
        meets_min_move = recent_atr_pct >= min_move_needed_pct
        return stop, target, meets_min_move

    # ── trade building ───────────────────────────────────────────────────────
    def _build_trade_record(self, symbol: str, entry: float, indicators: dict,
                            signal_score: float, rationale: str,
                            trade_type: str, **kwargs) -> Optional[dict]:
        try:
            atr = indicators.get("atr_14") or (entry * 0.02)
            tier = ALL_STOCKS.get(symbol, {}).get("tier", "large")
            stop, target, meets_min_move = self._stop_target_for_costs(entry, atr, tier)
            if not meets_min_move:
                return None
            from src.memory.journal import build_entry_snapshot

            entry_snapshot = build_entry_snapshot(
                {}, {"score": signal_score, "reasoning": rationale}, {}, {},
                indicators=indicators, extra_context={"regime": self._current_regime()})
            from src.analytics.strategy_attribution import classify_strategy

            trade = {
                "symbol": symbol, "entry": entry, "stop": stop, "target": target,
                "exit": None, "pnl_pct": None, "outcome": "OPEN",
                "signal_score": round(signal_score, 3),
                "opened_at": datetime.now(IST).isoformat(), "closed_at": None,
                "trade_type": trade_type, "rationale": rationale,
                "entry_snapshot": entry_snapshot,
                "is_forced": True, "market_was_closed": False,
                "tier": ALL_STOCKS.get(symbol, {}).get("tier", "large"),
                "strategy": classify_strategy(indicators),
            }
            try:
                from src.memory.company_dossier import dossier_store

                dossier_store.record_technical(symbol, indicators)
            except Exception:
                pass
            return trade
        except Exception:
            return None

    def _current_price(self, symbol: str) -> Optional[float]:
        current = self.fetcher.get_current_price(symbol)
        if current is None:
            df = self.fetcher.get_price_history(symbol, period="5d")
            current = float(df["Close"].iloc[-1]) if (df is not None and len(df)) else None
        return current

    def _close_forced_trade(self, trade: dict, current: Optional[float] = None) -> Optional[dict]:
        symbol = trade["symbol"]
        try:
            if current is None:
                current = self._current_price(symbol)
            if current is None:
                return None
            entry, stop, target = trade["entry"], trade["stop"], trade["target"]
            if current <= stop:
                outcome, exit_price = "LOSS", stop
            elif current >= target:
                outcome, exit_price = "WIN", target
            else:
                outcome = "WIN" if current > entry else "LOSS"
                exit_price = current
            gross_pnl_pct = round((exit_price - entry) / entry * 100, 2) if entry else 0.0
            tier = trade.get("tier") or ALL_STOCKS.get(symbol, {}).get("tier", "large")
            pnl_pct = net_pnl_pct(gross_pnl_pct, entry, exit_price, tier, is_intraday=True)
            closed = {**trade, "exit": exit_price, "pnl_pct": pnl_pct, "gross_pnl_pct": gross_pnl_pct,
                      "outcome": outcome, "closed_at": datetime.now(IST).isoformat()}
            try:
                from src.memory.company_dossier import dossier_store

                dossier_store.record_trade(symbol, {**closed, "source": "forced"})
            except Exception:
                pass
            return closed
        except Exception:
            return None

    @staticmethod
    def _quick_score(ind: dict) -> float:
        """Fast 0-1 signal score for RANKING only — even score=0 stocks get traded."""
        if not ind:
            return 0.0
        score = 0.0
        rsi = ind.get("rsi_14", 50) or 50
        trend = ind.get("trend", "SIDEWAYS") or "SIDEWAYS"
        adx = ind.get("adx_signal", "NEUTRAL") or "NEUTRAL"
        obv = ind.get("obv_trend", "NEUTRAL") or "NEUTRAL"
        supertrend = ind.get("supertrend_direction", "NEUTRAL") or "NEUTRAL"
        if 48 <= rsi <= 68:
            score += 0.25
        elif rsi < 35:
            score += 0.15  # oversold
        if trend == "UPTREND":
            score += 0.25
        elif trend == "DOWNTREND":
            score -= 0.10  # penalise but don't block
        if adx == "TRENDING":
            score += 0.20
        if obv == "RISING":
            score += 0.15
        if supertrend == "BULLISH":
            score += 0.15
        return round(max(0.0, min(1.0, score)), 3)

    @staticmethod
    def _build_rationale(symbol: str, ind: dict, score: float, tier: str) -> str:
        rsi = ind.get("rsi_14", 50) or 50
        trend = ind.get("trend", "SIDEWAYS") or "SIDEWAYS"
        adx = ind.get("adx_signal", "NEUTRAL") or "NEUTRAL"
        quality = ("strong" if score >= 0.6 else "moderate" if score >= 0.3
                   else "weak — forced learning entry")
        return f"FORCED ({tier}): {symbol} signal={quality} RSI={rsi:.0f} {trend} ADX={adx}"

    # ── learning sinks (knowledge base + simulation store) ───────────────────
    def _update_knowledge_from_trade(self, trade: dict) -> None:
        """Outcome analysis → knowledge base. FORCED_LEARNING category, segregated
        from real signal-confirmation patterns."""
        outcome = trade.get("outcome", "LOSS")
        symbol = trade.get("symbol", "")
        score = trade.get("signal_score", 0) or 0
        trade_type = trade.get("trade_type", "")
        score_bucket = "high" if score >= 0.6 else "mid" if score >= 0.3 else "low"
        pattern_id = f"forced-outcome-score{score_bucket}-{trade_type.lower()[:8]}"
        try:
            existing = [e for e in self.journal.get_active_knowledge(min_confidence=0.0)
                        if e.pattern_id == pattern_id]
            if existing:
                self.journal.update_knowledge_confidence(pattern_id, confirmed=(outcome == "WIN"))
                return
            self.journal.log_knowledge_entry(KnowledgeEntry(
                pattern_id=pattern_id,
                pattern_description=(f"FORCED OUTCOME: {score_bucket} signal ({score:.2f}) "
                                     f"→ {outcome} ({trade.get('pnl_pct', 0) or 0:+.1f}%)"),
                category="FORCED_LEARNING",
                confidence=0.06 if outcome == "WIN" else 0.04,
                observed_in_regime="ANY", observed_count=1, is_hypothesis=True,
                first_seen=datetime.now(IST), last_confirmed=datetime.now(IST),
                last_seen_in_trade=symbol, supporting_trades=json.dumps([symbol])))
        except Exception:
            pass

    def _log_simulation_row(self, trade: dict) -> None:
        """Mirror a closed forced trade into the SimulatedTrade store (tagged FORCED_*),
        already 'filled' so the ForwardSimulator never reprocesses it."""
        try:
            self.journal.log_simulated_trade(
                symbol=trade.get("symbol", ""), signal="BUY",
                strategy_name=f"FORCED_{trade.get('trade_type', '')}"[:40],
                regime=trade.get("regime", "ANY"),
                entry_price=trade.get("entry", 0) or 0,
                stop_price=trade.get("stop", 0) or 0,
                target_price=trade.get("target", 0) or 0,
                technical_score=trade.get("signal_score", 0) or 0,
                fundamental_score=0.0, judge_score=0.0,
                rejection_reason=f"FORCED_LEARNING: {(trade.get('rationale') or '')[:180]}",
                outcome_7d=trade.get("pnl_pct"),
                would_have_won=(trade.get("outcome") == "WIN"),
                learned_from=True)
        except Exception:
            pass

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _current_regime() -> str:
        """Tags every forced trade with the regime it was placed in — without
        this, the adaptive-thresholds feedback loop (src/memory/adaptive_thresholds.py)
        has no regime to group evidence by and never adjusts anything."""
        try:
            from src.data.regime_detector import RegimeDetector

            return RegimeDetector().detect().get("regime", "UNKNOWN")
        except Exception:
            return "UNKNOWN"

    @staticmethod
    def _is_market_hours(now: datetime) -> bool:
        total = now.hour * 60 + now.minute
        return now.weekday() < 5 and 9 * 60 + 15 <= total <= 15 * 60 + 30

    def _count_todays_forced_trades(self) -> int:
        today = datetime.now(IST).date().isoformat()
        opens = sum(1 for t in self._load_open_forced_trades()
                    if t.get("opened_at", "")[:10] == today)
        closed = sum(1 for t in self._load_history()
                     if t.get("opened_at", "")[:10] == today)
        return opens + closed

    def _load_open_forced_trades(self) -> list:
        return self._read_json(FORCED_TRADE_FILE, [])

    def _load_history(self) -> list:
        return self._read_json(FORCED_HISTORY_FILE, [])

    def _save_forced_trade(self, trade: dict) -> None:
        if trade.get("outcome") == "OPEN":
            opens = self._load_open_forced_trades()
            opens.append(trade)
            self._write_json(FORCED_TRADE_FILE, opens)
        else:  # already closed (historical sim) → straight to history
            history = self._load_history()
            history.append(trade)
            self._write_json(FORCED_HISTORY_FILE, history[-HISTORY_RETENTION:])

    @staticmethod
    def _read_json(path: Path, default):
        try:
            if path.exists():
                return json.loads(path.read_text())
        except Exception:
            pass
        return default

    @staticmethod
    def _write_json(path: Path, data) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, default=str))
        except OSError:
            pass
