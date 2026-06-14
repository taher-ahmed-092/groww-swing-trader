"""
Order deduplication — a file-based guard against accidental duplicate orders
(double-clicks, retries) within a short window. Never raises.
"""
from __future__ import annotations

import json
import os
import time

_LOCK_FILE = os.path.join("data", "cache", "pending_orders.json")
_WINDOW_SECONDS = 60


class OrderDeduplicator:
    def __init__(self, window_seconds: int = _WINDOW_SECONDS) -> None:
        self.window = window_seconds
        os.makedirs(os.path.dirname(_LOCK_FILE), exist_ok=True)

    def _load(self) -> dict:
        try:
            if os.path.exists(_LOCK_FILE):
                with open(_LOCK_FILE, encoding="utf-8") as f:
                    return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _save(self, data: dict) -> None:
        try:
            with open(_LOCK_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass

    def is_duplicate(self, symbol: str, side: str, qty: int, price: float) -> bool:
        """True if an identical order was recorded within the dedup window."""
        key = f"{symbol}:{side}:{qty}:{round(price, 2)}"
        now = time.time()
        data = {k: v for k, v in self._load().items() if now - v < self.window}
        dup = key in data
        if not dup:
            data[key] = now
        self._save(data)
        return dup
