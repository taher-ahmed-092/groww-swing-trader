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
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from src.agents.technical.indicators import compute_indicators
from src.data.fetcher import MarketDataFetcher
from src.data.watchlist import ALL_STOCKS, LARGE_CAP, MID_CAP, SMALL_CAP
from src.memory.journal import KnowledgeEntry, TradingJournal

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# Forced-trade universe: liquid large-cap + volatile mid-cap + opportunity small-cap.
FORCED_TRADE_UNIVERSE = (
    list(LARGE_CAP.keys())[:15] + list(MID_CAP.keys())[:15] + list(SMALL_CAP.keys())[:10]
)

FORCED_TRADE_FILE = Path("data/cache/forced_trades_today.json")
FORCED_HISTORY_FILE = Path("data/cache/forced_trades_history.json")


class AlwaysOnTrader:
    TARGET_DAILY_TRADES = 3   # minimum learning trades per day
    MAX_DAILY_TRADES = 6      # don't overdo it

    def __init__(self) -> None:
        self.journal = TradingJournal()
        self.fetcher = MarketDataFetcher()

    # ── public API ───────────────────────────────────────────────────────────
    def ensure_daily_trades(self) -> list[dict]:
        """Place forced trades until today's count reaches the daily target.
        Up to 3 per call. Market hours → live forced; otherwise → historical sim."""
        placed_today = self._count_todays_forced_trades()
        needed = self.TARGET_DAILY_TRADES - placed_today
        if needed <= 0:
            return []

        is_market_hours = self._is_market_hours(datetime.now(IST))
        trades: list[dict] = []
        for _ in range(min(needed, 3)):
            trade = (self._place_live_forced_trade() if is_market_hours
                     else self._place_historical_simulation_trade())
            if not trade:
                continue
            trades.append(trade)
            self._save_forced_trade(trade)
            # Historical sims close immediately → learn now. Live forced trades are
            # OPEN and learn later when close_open_forced_trades() runs.
            if trade.get("outcome") not in (None, "OPEN"):
                self._update_knowledge_from_trade(trade)
                self._log_simulation_row(trade)
        return trades

    def close_open_forced_trades(self) -> list[dict]:
        """Close forced trades opened 45+ min ago, or any still open at/after 15:20 IST."""
        open_trades = self._load_open_forced_trades()
        if not open_trades:
            return []

        now = datetime.now(IST)
        market_closing = now.replace(hour=15, minute=20, second=0, microsecond=0)
        closed, still_open = [], []
        for trade in open_trades:
            try:
                opened_at = datetime.fromisoformat(trade["opened_at"])
                elapsed_min = (now - opened_at).total_seconds() / 60
            except Exception:
                elapsed_min = 999
            if elapsed_min >= 45 or now >= market_closing:
                result = self._close_forced_trade(trade)
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
            self._write_json(FORCED_HISTORY_FILE, history[-200:])
        return closed

    def get_todays_summary(self) -> dict:
        history = self._load_history()
        today = date.today().isoformat()
        todays = [t for t in history if t.get("opened_at", "")[:10] == today]
        wins = sum(1 for t in todays if t.get("outcome") == "WIN")
        total = len(todays)
        return {
            "total": total, "wins": wins, "losses": total - wins,
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "recent": todays[-5:],
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

    def _score_all_candidates_live(self) -> list[dict]:
        sample = random.sample(FORCED_TRADE_UNIVERSE, min(40, len(FORCED_TRADE_UNIVERSE)))
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
            df = self.fetcher.get_price_history(symbol, period="30d")
            if df is None or len(df) < 10:
                return None
            entry_idx = len(df) - 2  # enter at second-to-last close, verify on the rest
            if entry_idx < 5:
                return None
            df_at_entry = df.iloc[: entry_idx + 1]
            df_after = df.iloc[entry_idx + 1:]

            entry = float(df_at_entry["Close"].iloc[-1])
            ind = compute_indicators(df_at_entry)
            score = self._quick_score(ind)
            atr = ind.get("atr_14") or (entry * 0.02)
            stop = max(round(entry - 1.5 * atr, 2), round(entry * 0.93, 2))  # ≤7% stop
            target = round(entry + 2.0 * (entry - stop), 2)

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
            pnl_pct = round((exit_price - entry) / entry * 100, 2) if entry else 0.0

            now_iso = datetime.now(IST).isoformat()
            return {
                "symbol": symbol, "entry": entry, "stop": stop, "target": target,
                "exit": exit_price, "pnl_pct": pnl_pct, "outcome": outcome,
                "signal_score": score, "opened_at": now_iso, "closed_at": now_iso,
                "trade_type": "HISTORICAL_SIM",
                "rationale": (f"Historical sim: {symbol} RSI {(ind.get('rsi_14') or 50):.0f} "
                              f"→ {outcome} ({pnl_pct:+.1f}%)"),
                "is_forced": True, "market_was_closed": True,
                "tier": ALL_STOCKS.get(symbol, {}).get("tier", "large"),
            }
        except Exception:
            return None

    # ── trade building ───────────────────────────────────────────────────────
    def _build_trade_record(self, symbol: str, entry: float, indicators: dict,
                            signal_score: float, rationale: str,
                            trade_type: str, **kwargs) -> Optional[dict]:
        try:
            atr = indicators.get("atr_14") or (entry * 0.02)
            stop = max(round(entry - 1.5 * atr, 2), round(entry * 0.93, 2))  # ≤7% stop
            target = round(entry + 2.0 * (entry - stop), 2)
            return {
                "symbol": symbol, "entry": entry, "stop": stop, "target": target,
                "exit": None, "pnl_pct": None, "outcome": "OPEN",
                "signal_score": round(signal_score, 3),
                "opened_at": datetime.now(IST).isoformat(), "closed_at": None,
                "trade_type": trade_type, "rationale": rationale,
                "is_forced": True, "market_was_closed": False,
                "tier": ALL_STOCKS.get(symbol, {}).get("tier", "large"),
            }
        except Exception:
            return None

    def _close_forced_trade(self, trade: dict) -> Optional[dict]:
        symbol = trade["symbol"]
        try:
            current = self.fetcher.get_current_price(symbol)
            if current is None:
                df = self.fetcher.get_price_history(symbol, period="5d")
                current = float(df["Close"].iloc[-1]) if (df is not None and len(df)) else None
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
            pnl_pct = round((exit_price - entry) / entry * 100, 2) if entry else 0.0
            return {**trade, "exit": exit_price, "pnl_pct": pnl_pct,
                    "outcome": outcome, "closed_at": datetime.now(IST).isoformat()}
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
    def _is_market_hours(now: datetime) -> bool:
        total = now.hour * 60 + now.minute
        return now.weekday() < 5 and 9 * 60 + 15 <= total <= 15 * 60 + 30

    def _count_todays_forced_trades(self) -> int:
        today = date.today().isoformat()
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
            self._write_json(FORCED_HISTORY_FILE, history[-200:])

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
