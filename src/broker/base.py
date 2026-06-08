"""Abstract broker interface. All brokers (paper + live) implement this."""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

from config.risk_limits import LIMITS


class BrokerBase(ABC):
    @abstractmethod
    def place_order(
        self,
        symbol: str,
        quantity: int,
        order_type: str,
        price: float,
        stop_price: float,
        target_price: float,
    ) -> dict:
        """Place an order. Implementations MUST attach a protective stop."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> dict:
        ...

    @abstractmethod
    def get_positions(self) -> list[dict]:
        ...

    @abstractmethod
    def get_order_status(self, order_id: str) -> dict:
        ...

    def check_kill_switch(self) -> None:
        """Raise immediately if the kill switch file is present."""
        if os.path.exists(LIMITS.kill_switch_file):
            raise RuntimeError(
                f"KILL_SWITCH file present ({LIMITS.kill_switch_file}) — execution halted."
            )
