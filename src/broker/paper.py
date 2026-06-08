"""Paper broker — simulates order placement with zero real API calls."""
from __future__ import annotations

import uuid

from rich.console import Console

from src.broker.base import BrokerBase

console = Console()


class PaperBroker(BrokerBase):
    def __init__(self) -> None:
        self._positions: dict[str, dict] = {}
        self._orders: dict[str, dict] = {}

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

        color = "green" if order_type.upper() == "BUY" else "red"
        console.print(
            f"[{color}][PAPER] {order_type.upper()} {quantity}x {symbol} @ {price:.2f} "
            f"| Stop: {stop_price:.2f} | Target: {target_price:.2f}[/{color}]"
        )

        order_id = str(uuid.uuid4())
        order = {
            "order_id": order_id,
            "symbol": symbol,
            "quantity": quantity,
            "order_type": order_type.upper(),
            "status": "PAPER_FILLED",
            "fill_price": price,
            "stop_price": stop_price,
            "target_price": target_price,
            "broker_mode": "paper",
        }
        self._orders[order_id] = order
        self._positions[symbol] = {
            "symbol": symbol,
            "quantity": quantity,
            "avg_price": price,
            "stop_price": stop_price,
            "target_price": target_price,
        }
        return order

    def cancel_order(self, order_id: str) -> dict:
        self.check_kill_switch()
        order = self._orders.get(order_id)
        if order:
            order["status"] = "CANCELLED"
            return order
        return {"order_id": order_id, "status": "NOT_FOUND"}

    def get_positions(self) -> list[dict]:
        return list(self._positions.values())

    def get_order_status(self, order_id: str) -> dict:
        return self._orders.get(order_id, {"order_id": order_id, "status": "NOT_FOUND"})
