"""
Executor agent — the only place orders are placed.

Picks PaperBroker by default; GrowwBroker only when settings.broker_mode == "live".
Every execution is journaled. Honors the kill switch.
"""
from __future__ import annotations

from rich.console import Console

from config.settings import settings
from src.broker.base import BrokerBase
from src.broker.paper import PaperBroker
from src.memory.journal import TradingJournal
from src.notifications.telegram_bot import TelegramNotifier
from src.orchestrator.state import TradeState

console = Console()


class ExecutorAgent:
    def __init__(self) -> None:
        self.journal = TradingJournal()

    def _select_broker(self) -> BrokerBase:
        if settings.broker_mode == "paper":
            return PaperBroker()
        # Imported lazily: growwapi import has side effects / requires live config.
        from src.broker.groww import GrowwBroker

        return GrowwBroker()

    def execute(self, state: TradeState) -> dict:
        # 1. Kill switch.
        broker = self._select_broker()
        broker.check_kill_switch()

        # Risk gate must have approved and produced an order spec.
        decision = state.get("trade_decision") or {}
        risk = state.get("risk_check") or {}
        if not risk.get("approved") or not decision:
            console.print("[yellow][EXECUTOR] Risk not approved / no order spec — skipping.[/yellow]")
            return {
                "order_id": None,
                "status": "SKIPPED_RISK_REJECTED",
                "fill_price": None,
                "broker_mode": settings.broker_mode,
            }

        # Entry-window gate — don't chase a setup whose optimal window has passed.
        # LIVE only: in paper/demo we still want to exercise execution at any hour,
        # so we compute & display the window but don't block (Phase 1 unblock intent).
        time_window = state.get("time_window", {}) or {}
        if settings.live_trading_enabled and time_window.get("current_status") == "MISSED":
            console.print("[yellow][EXECUTOR] Entry window missed — auto-cancelled.[/yellow]")
            return {
                "order_id": None,
                "status": "CANCELLED_WINDOW_MISSED",
                "fill_price": None,
                "broker_mode": settings.broker_mode,
                "reason": time_window.get("countdown_display", "Entry window closed"),
            }

        # Live mode: human-in-the-loop approval before any real order.
        if settings.broker_mode == "live":
            notifier = TelegramNotifier()
            notifier.send_trade_card(state)
            approved = notifier.wait_for_approval(
                settings.auto_approve_timeout_seconds, symbol=state.get("symbol", "")
            )
            if not approved:
                console.print("[red][EXECUTOR] Trade rejected at human approval gate.[/red]")
                return {
                    "order_id": None,
                    "status": "REJECTED_AT_APPROVAL",
                    "fill_price": None,
                    "broker_mode": settings.broker_mode,
                    "error": "Trade rejected at human approval gate (timeout or manual reject)",
                }

        # Journal the proposal before placing.
        record = self.journal.log_proposed(state)

        # 2 & 3. Place the order through the selected broker.
        result = broker.place_order(
            symbol=decision["symbol"],
            quantity=decision["quantity"],
            order_type=decision.get("order_type", "BUY"),
            price=decision["price"],
            stop_price=decision["stop_price"],
            target_price=decision["target_price"],
        )

        # Realistic paper fill: a real order never fills at the exact signal price.
        # Record both so cost/expectancy analysis reflects reality, not a fiction
        # where every simulated entry is frictionless.
        signal_price = decision["price"]
        if settings.broker_mode == "paper":
            from src.data.watchlist import ALL_STOCKS
            from src.trading.cost_model import SLIPPAGE_BY_TIER

            tier = ALL_STOCKS.get(decision["symbol"], {}).get("tier", "large")
            fill_price = round(signal_price * (1 + SLIPPAGE_BY_TIER.get(tier, 0.0005)), 4)
            result["fill_price"] = fill_price

        # 4. Journal the execution.
        self.journal.log_executed(record.id, result)
        try:
            self.journal.log_fill_prices(record.id, signal_price, result.get("fill_price"))
        except Exception:
            pass

        # Live dashboard trade-plane animation (best-effort — the dashboard may
        # not be running as a separate process; never blocks execution).
        try:
            import asyncio

            from dashboard.server import broadcast_trade_event

            asyncio.run(broadcast_trade_event("TRADE_OPENED", {
                "symbol": decision["symbol"], "entry": result.get("fill_price") or decision["price"],
                "stop": decision["stop_price"], "target": decision["target_price"],
                "source": "real",
            }))
        except Exception:
            pass

        # 5. Return the result (carry the journal trade_id for downstream RCA/closure).
        result["trade_id"] = record.id
        return result
