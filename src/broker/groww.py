"""
Live broker backed by the Groww Trading API.

Requires:
  - LIVE_TRADING_ENABLED=true in .env
  - GROWW_API_KEY / GROWW_API_SECRET / GROWW_ACCESS_TOKEN set
  - A whitelisted static IP registered with Groww (orders are rejected otherwise)

If the growwapi SDK is not installed, install it manually:
    uv add growwapi
See: https://groww.in/trade-api/docs/python-sdk
"""
from __future__ import annotations

from rich.console import Console

from config.settings import settings
from src.broker.base import BrokerBase

console = Console()

try:
    from growwapi import GrowwAPI  # type: ignore
except ImportError as exc:  # pragma: no cover - exercised only without the SDK
    raise ImportError(
        "growwapi SDK is not installed. Install it with `uv add growwapi`. "
        "Docs: https://groww.in/trade-api/docs/python-sdk"
    ) from exc


class GrowwBroker(BrokerBase):
    def __init__(self) -> None:
        if not settings.live_trading_enabled:
            raise ValueError(
                "Live trading is OFF. Set LIVE_TRADING_ENABLED=true in .env and ensure "
                "Groww keys + static IP are configured."
            )
        if not settings.has_groww_credentials:
            raise ValueError(
                "Groww credentials missing. Set GROWW_API_KEY, GROWW_API_SECRET, and "
                "GROWW_ACCESS_TOKEN in .env."
            )
        self._client = GrowwAPI(settings.groww_access_token)

    def place_order(
        self,
        symbol: str,
        quantity: int,
        order_type: str,
        price: float,
        stop_price: float,
        target_price: float,
    ) -> dict:
        self.check_kill_switch()

        # Non-negotiable: every live order carries a protective stop.
        if not stop_price:
            raise ValueError(
                f"Refusing to place {symbol} order without a stop_price. "
                "Every trade MUST have a stop (CLAUDE.md rule 2)."
            )

        # Guard against accidental duplicate orders within a 60s window.
        from src.broker.order_deduplication import OrderDeduplicator
        if OrderDeduplicator().is_duplicate(symbol, order_type.upper(), quantity, price):
            console.print(f"[yellow][GROWW] Duplicate order suppressed for {symbol}.[/yellow]")
            return {"status": "DUPLICATE_SUPPRESSED", "symbol": symbol, "broker_mode": "live"}

        try:
            # Entry order.
            entry = self._client.place_order(
                trading_symbol=symbol,
                quantity=quantity,
                transaction_type=order_type.upper(),
                order_type="LIMIT",
                price=price,
                product="DELIVERY",
            )
            # GTT OCO (One-Cancels-Other): stop leg + target leg.
            # Either leg firing cancels the other.
            oco = self._client.place_gtt_order(
                trading_symbol=symbol,
                quantity=quantity,
                transaction_type="SELL",
                trigger_type="OCO",
                stop_loss_price=stop_price,
                target_price=target_price,
            )
            return {
                "order_id": entry.get("order_id") if isinstance(entry, dict) else entry,
                "oco_order": oco,
                "status": "LIVE_PLACED",
                "fill_price": price,
                "broker_mode": "live",
            }
        except Exception as exc:
            console.print(f"[red][GROWW] Order placement failed for {symbol}: {exc}[/red]")
            raise

    def cancel_order(self, order_id: str) -> dict:
        self.check_kill_switch()
        try:
            return self._client.cancel_order(order_id=order_id)
        except Exception as exc:
            console.print(f"[red][GROWW] Cancel failed for {order_id}: {exc}[/red]")
            raise

    def get_positions(self) -> list[dict]:
        try:
            positions = self._client.get_positions()
            return positions if isinstance(positions, list) else [positions]
        except Exception as exc:
            console.print(f"[red][GROWW] get_positions failed: {exc}[/red]")
            raise

    def get_order_status(self, order_id: str) -> dict:
        try:
            return self._client.get_order_status(order_id=order_id)
        except Exception as exc:
            console.print(f"[red][GROWW] get_order_status failed for {order_id}: {exc}[/red]")
            raise
