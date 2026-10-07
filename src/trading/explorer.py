"""
EXPLORER — the unfiltered control group.

Enters random watchlist stocks with NO entry filters and none of the sector,
exposure or loss-halt gates, so its closed trades show what each gate in the
other engines actually protects (see src/analytics/filter_value.py). The 7%
stop and 2:1 target are kept so every trade can close.

Simulation store only: JSON open/history files, never TradeRecord, never the
broker. Its history file is deliberately absent from trade_loader, the ML
training sets and AdaptiveThresholds, so it stays out of headline metrics and
cannot throttle or retrain any other engine.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from config.risk_limits import LIMITS
from src.agents.technical.indicators import compute_indicators
from src.analytics.filter_value import entry_filter_flags
from src.data.fetcher import MarketDataFetcher
from src.data.watchlist import ALL_STOCKS
from src.memory.journal import build_entry_snapshot
from src.trading.always_on_trader import AlwaysOnTrader
from src.trading.cost_model import net_pnl_pct

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

ENGINE_NAME = "EXPLORER"
EXPLORER_OPEN_FILE = Path("data/cache/explorer_open.json")
EXPLORER_HISTORY_FILE = Path("data/cache/explorer_history.json")
HISTORY_RETENTION = 10000
STOP_PCT = 7.0
TARGET_RR = 2.0


class ExplorerTrader:
    MAX_OPEN = 100
    MAX_DAILY_TRADES = 200
    MAX_HOLD_DAYS = 5
    ENTRIES_PER_CALL = 10

    def __init__(self) -> None:
        self.fetcher = MarketDataFetcher()
        self.last_block_reason: Optional[str] = None

    # ── public API ───────────────────────────────────────────────────────────
    def place_entries(self) -> list[dict]:
        """Opens up to ENTRIES_PER_CALL unfiltered positions, bounded by the
        open and daily caps. Market hours only — entry prices are live closes."""
        self.last_block_reason = None
        if Path(LIMITS.kill_switch_file).exists():
            self.last_block_reason = "kill_switch"
            return []
        if not AlwaysOnTrader._is_market_hours(datetime.now(IST)):
            self.last_block_reason = "market_closed"
            return []
        open_trades = self._load_open()
        slots = min(self.ENTRIES_PER_CALL,
                    self.MAX_OPEN - len(open_trades),
                    self.MAX_DAILY_TRADES - self._count_todays())
        if slots <= 0:
            self.last_block_reason = "cap"
            return []

        held = {t["symbol"] for t in open_trades}
        pool = [s for s in ALL_STOCKS if s not in held]
        regime = AlwaysOnTrader._current_regime()
        placed: list[dict] = []
        for symbol in random.sample(pool, min(len(pool), slots * 3)):
            if len(placed) >= slots:
                break
            trade = self._build_trade(symbol, regime)
            if trade:
                placed.append(trade)
        if not placed:
            self.last_block_reason = "no_price_data"
            return []
        AlwaysOnTrader._write_json(EXPLORER_OPEN_FILE, open_trades + placed)
        return placed

    def close_open_trades(self) -> list[dict]:
        """Closes on stop, target, or MAX_HOLD_DAYS — checked on every call."""
        open_trades = self._load_open()
        if not open_trades:
            return []
        now = datetime.now(IST)
        closed, still_open = [], []
        for trade in open_trades:
            try:
                elapsed = (now.date() - datetime.fromisoformat(trade["opened_at"]).date()).days
            except Exception:
                elapsed = self.MAX_HOLD_DAYS
            price = self.fetcher.get_current_price(trade["symbol"])
            if price is None:
                still_open.append(trade)
                continue
            if not (price <= trade["stop"] or price >= trade["target"]
                    or elapsed >= self.MAX_HOLD_DAYS):
                still_open.append(trade)
                continue
            closed.append(self._close(trade, price))

        AlwaysOnTrader._write_json(EXPLORER_OPEN_FILE, still_open)
        if closed:
            history = AlwaysOnTrader._read_json(EXPLORER_HISTORY_FILE, []) + closed
            AlwaysOnTrader._write_json(EXPLORER_HISTORY_FILE, history[-HISTORY_RETENTION:])
        return closed

    # ── internals ────────────────────────────────────────────────────────────
    def _build_trade(self, symbol: str, regime: str) -> Optional[dict]:
        try:
            df = self.fetcher.get_price_history(symbol)
            if df is None or len(df) < 30:
                return None
            entry = float(df["Close"].iloc[-1])
            if entry <= 0:
                return None
            ind = compute_indicators(df)
            tier = ALL_STOCKS.get(symbol, {}).get("tier", "large")
            stop = round(entry * (1 - STOP_PCT / 100), 2)
            target = round(entry * (1 + STOP_PCT * TARGET_RR / 100), 2)
            trade = {
                "symbol": symbol, "entry": entry, "stop": stop, "target": target,
                "exit": None, "pnl_pct": None, "outcome": "OPEN",
                "opened_at": datetime.now(IST).isoformat(), "closed_at": None,
                "trade_type": ENGINE_NAME, "strategy": ENGINE_NAME, "engine": ENGINE_NAME,
                "rationale": f"EXPLORER ({tier}): {symbol} unfiltered entry",
                "tier": tier, "regime": regime, "is_forced": True,
                "filters": entry_filter_flags(ind, tier, regime),
                "entry_snapshot": build_entry_snapshot(
                    {}, {"reasoning": "unfiltered explorer entry"}, {}, {},
                    indicators=ind, extra_context={"regime": regime}),
            }
        except Exception as exc:
            log.debug("explorer entry %s failed: %s", symbol, exc)
            return None
        try:
            from src.memory.company_dossier import dossier_store

            dossier_store.record_technical(symbol, ind)
        except Exception:
            pass
        return trade

    @staticmethod
    def _close(trade: dict, price: float) -> dict:
        entry, stop, target = trade["entry"], trade["stop"], trade["target"]
        if price <= stop:
            outcome, exit_price = "LOSS", stop
        elif price >= target:
            outcome, exit_price = "WIN", target
        else:
            outcome, exit_price = ("WIN" if price > entry else "LOSS"), price
        gross = round((exit_price - entry) / entry * 100, 2) if entry else 0.0
        pnl = net_pnl_pct(gross, entry, exit_price, trade.get("tier", "large"), is_intraday=False)
        closed = {**trade, "exit": exit_price, "pnl_pct": pnl, "gross_pnl_pct": gross,
                  "outcome": outcome, "closed_at": datetime.now(IST).isoformat()}
        try:
            from src.memory.company_dossier import dossier_store

            dossier_store.record_trade(trade["symbol"], {**closed, "source": "explorer"})
        except Exception:
            pass
        return closed

    def _count_todays(self) -> int:
        today = datetime.now(IST).date().isoformat()
        rows = self._load_open() + AlwaysOnTrader._read_json(EXPLORER_HISTORY_FILE, [])
        return sum(1 for t in rows if t.get("opened_at", "")[:10] == today)

    @staticmethod
    def _load_open() -> list:
        return AlwaysOnTrader._read_json(EXPLORER_OPEN_FILE, [])
