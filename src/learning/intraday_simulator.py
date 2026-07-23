"""
Intraday learning simulator — generates 3-5 paper trades every market day.

The real pipeline (correctly) refuses to buy in a downtrending Nifty, which means
in a bear market the system gathers almost no learning data. This module closes
that gap: it opens simulated intraday positions on liquid large-caps using REAL
NSE 5-minute data, closes them the same afternoon, and feeds every outcome back
into the knowledge base. Wins confirm patterns; losses seed avoid-this hypotheses.

These are NOT real trades and NEVER touch the broker. They exist purely so the
system keeps learning regardless of market direction. State is JSON files under
data/cache/ (open positions + a rolling 100-entry history).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from config.risk_limits import LIMITS
from src.agents.technical.indicators import compute_indicators
from src.data.fetcher import MarketDataFetcher
from src.data.market_calendar import NSECalendar
from src.data.regime_detector import RegimeDetector
from src.memory.journal import KnowledgeEntry, TradingJournal
from src.trading.cost_model import net_pnl_pct

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
_OPEN_FILE = Path("data/cache/intraday_sim_open.json")
_HISTORY_FILE = Path("data/cache/intraday_sim_history.json")


class IntradaySimulator:
    # Always simulate these liquid large-caps, regardless of scout output.
    STOCKS_TO_SIMULATE = [
        "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
        "BAJAJ-AUTO", "SUNPHARMA", "WIPRO", "LT", "TITAN",
    ]

    def __init__(self) -> None:
        self.fetcher = MarketDataFetcher()

    def _get_simulation_candidates(self) -> list[str]:
        """Today's candidates from the expanded watchlist — a randomized mix of
        large/mid/small caps so the system sees different stocks each day. Falls back
        to the fixed large-cap list if the watchlist can't be loaded."""
        try:
            import random

            from src.data.watchlist import ALL_STOCKS

            large = [s for s, d in ALL_STOCKS.items() if d["tier"] == "large"]
            mid = [s for s, d in ALL_STOCKS.items() if d["tier"] == "mid"]
            small = [s for s, d in ALL_STOCKS.items() if d["tier"] == "small"]
            return (random.sample(large, min(3, len(large)))
                    + random.sample(mid, min(3, len(mid)))
                    + random.sample(small, min(2, len(small))))
        except Exception:
            return list(self.STOCKS_TO_SIMULATE)

    # ── morning: open positions ─────────────────────────────────────────────────
    def run_morning_entries(self) -> list[dict]:
        """9:30 AM: open 3-5 intraday simulated positions on real data."""
        if not NSECalendar().is_market_open():
            return []

        positions: list[dict] = []
        try:
            regime = RegimeDetector().detect()
        except Exception:
            regime = {"regime": "UNKNOWN"}
        regime_name = regime.get("regime", "UNKNOWN")

        for symbol in self._get_simulation_candidates():
            if len(positions) >= 5:
                break

            df = self._get_intraday(symbol)
            if df is None or len(df) < 15:
                continue

            indicators = compute_indicators(df)
            rsi = indicators.get("rsi_14", 50) or 50
            trend = indicators.get("trend", "SIDEWAYS")
            adx = indicators.get("adx_signal", "NEUTRAL")

            # Simulation entry criteria — a lower bar than real trading (the point
            # is to gather diverse outcomes, not to protect capital).
            if regime_name in ("BULL_TRENDING", "TRANSITIONAL"):
                should_enter = 50 <= rsi <= 68 and trend in ("UPTREND", "SIDEWAYS")
            elif regime_name in ("RANGE_BOUND", "VOLATILE"):
                should_enter = rsi < 38  # mean reversion
            else:
                should_enter = rsi < 45  # any oversold bounce
            if not should_enter:
                continue

            entry = float(df["Close"].iloc[-1])
            if entry <= 0:
                continue

            stop = round(entry * (1 - LIMITS.stop_loss_pct / 100), 2)
            target = round(entry + (entry - stop) * 1.2, 2)  # 1.2:1 for intraday

            positions.append({
                "symbol": symbol,
                "entry": entry,
                "stop": stop,
                "target": target,
                "entry_time": datetime.now(IST).isoformat(),
                "regime": regime_name,
                "rsi_at_entry": round(rsi, 1),
                "trend_at_entry": trend,
                "adx_at_entry": adx,
            })

        self._write_json(_OPEN_FILE, positions)
        log.info("Opened %d intraday simulations", len(positions))
        return positions

    # ── afternoon: close + learn ────────────────────────────────────────────────
    def run_afternoon_exits(self) -> dict:
        """3:15 PM: close all sims, compute P&L, update the knowledge base."""
        empty = {"closed": 0, "wins": 0, "losses": 0, "results": []}
        positions = self._read_json(_OPEN_FILE, default=[])
        if not positions:
            return empty

        journal = TradingJournal()
        wins = losses = 0
        results: list[dict] = []

        for pos in positions:
            symbol = pos["symbol"]
            entry = pos["entry"]
            current = self.fetcher.get_current_price(symbol)
            if current is None:
                current = entry  # flat if no price available

            if current >= pos["target"]:
                outcome, exit_price = "WIN", pos["target"]
            elif current <= pos["stop"]:
                outcome, exit_price = "LOSS", pos["stop"]
            else:
                exit_price = current
                outcome = "WIN" if current > entry else "LOSS"
            gross_pnl_pct = round((exit_price - entry) / entry * 100, 2) if entry else 0.0
            try:
                from src.data.watchlist import ALL_STOCKS

                tier = ALL_STOCKS.get(symbol, {}).get("tier", "large")
            except Exception:
                tier = "large"
            pnl_pct = net_pnl_pct(gross_pnl_pct, entry, exit_price, tier, is_intraday=True)

            if outcome == "WIN":
                wins += 1
            else:
                losses += 1

            self._learn(journal, pos, outcome)

            results.append({
                **pos,
                "exit": exit_price,
                "pnl_pct": pnl_pct,
                "gross_pnl_pct": gross_pnl_pct,
                "outcome": outcome,
                "closed_at": datetime.now(IST).isoformat(),
            })

        self._write_json(_OPEN_FILE, [])

        history = self._read_json(_HISTORY_FILE, default=[])[-95:]
        self._write_json(_HISTORY_FILE, history + results)

        total = wins + losses
        if total:
            log.info("Intraday sim: %dW/%dL, WR %.0f%%", wins, losses, wins / total * 100)
        return {"closed": total, "wins": wins, "losses": losses, "results": results}

    def _learn(self, journal: TradingJournal, pos: dict, outcome: str) -> None:
        """Every simulated trade updates the knowledge base. Wins confirm an existing
        pattern (or seed a new positive one); losses seed an avoid-this hypothesis."""
        regime = pos["regime"]
        symbol = pos["symbol"]
        pattern_id = f"sim-{symbol}-{regime}-rsi{int(pos['rsi_at_entry'])}"
        now = datetime.now(IST)

        if outcome == "WIN":
            for e in journal.get_active_knowledge(min_confidence=0.0):
                if regime in (e.observed_in_regime or "") and symbol in e.pattern_description:
                    journal.update_knowledge_confidence(e.pattern_id, confirmed=True)
                    return
            journal.log_knowledge_entry(KnowledgeEntry(
                pattern_id=pattern_id + "-win",
                pattern_description=(
                    f"{symbol} BUY signal worked in {regime} "
                    f"(RSI {pos['rsi_at_entry']}, {pos['trend_at_entry']})"
                ),
                category="INTRADAY_SIMULATION",
                confidence=0.08,
                observed_in_regime=regime,
                observed_count=1,
                is_hypothesis=True,
                first_seen=now,
                last_confirmed=now,
                last_seen_in_trade=symbol,
                supporting_trades=json.dumps([symbol]),
            ))
        else:
            journal.log_knowledge_entry(KnowledgeEntry(
                pattern_id=pattern_id + "-loss",
                pattern_description=(
                    f"{symbol} BUY failed in {regime} "
                    f"(RSI {pos['rsi_at_entry']}, {pos['trend_at_entry']}) — avoid similar setups"
                ),
                category="INTRADAY_SIMULATION",
                confidence=0.06,
                observed_in_regime=regime,
                observed_count=1,
                is_hypothesis=True,
                first_seen=now,
                last_confirmed=now,
                last_seen_in_trade=symbol,
                supporting_trades=json.dumps([symbol]),
            ))

    # ── reads for dashboard / Telegram ──────────────────────────────────────────
    def get_summary(self) -> dict:
        history = self._read_json(_HISTORY_FILE, default=[])
        total = len(history)
        if not total:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "recent": []}
        wins = sum(1 for r in history if r.get("outcome") == "WIN")
        return {
            "total": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total * 100, 1),
            "recent": history[-5:],
        }

    # ── helpers ─────────────────────────────────────────────────────────────────
    def _get_intraday(self, symbol: str) -> pd.DataFrame | None:
        try:
            import yfinance as yf

            df = yf.Ticker(symbol + ".NS").history(period="1d", interval="5m")
            if df is None or len(df) < 15:
                return None
            return df.dropna()
        except Exception:
            return None

    @staticmethod
    def _read_json(path: Path, default):
        try:
            if path.exists():
                return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
        return default

    @staticmethod
    def _write_json(path: Path, data) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, default=str))
        except OSError:
            pass
