"""
Signal quality tracker — learns which individual signals predict wins, per sector.

Over time the system knows "hammers win 68% for Banking" and feeds that back into
the technical agent's prompt and the dashboard.
"""
from __future__ import annotations

import json
import os

_TRACKER_FILE = os.path.join("data", "cache", "signal_accuracy.json")


class SignalTracker:
    TRACKER_FILE = _TRACKER_FILE

    def _load(self) -> dict:
        try:
            if os.path.exists(self.TRACKER_FILE):
                with open(self.TRACKER_FILE, encoding="utf-8") as f:
                    return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _save(self, data: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self.TRACKER_FILE), exist_ok=True)
            with open(self.TRACKER_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    def update_from_trade(self, trade, state_snapshot: dict) -> None:
        tech = (state_snapshot or {}).get("technical_verdict", {})
        indics = tech.get("indicators", {}) or {}
        sector = (state_snapshot or {}).get("sector", "Unknown") or "Unknown"
        won = trade.outcome == "WIN"

        signals: list[str] = []
        rsi = indics.get("rsi_14") or 0
        if 50 <= rsi <= 60:
            signals.append("rsi_50_60")
        elif 60 < rsi <= 65:
            signals.append("rsi_60_65")
        if indics.get("trend") == "UPTREND":
            signals.append("uptrend")
        if indics.get("adx_signal") == "TRENDING":
            signals.append("adx_trending")
        if indics.get("obv_trend") == "RISING":
            signals.append("obv_rising")
        for pattern in tech.get("patterns", []) or []:
            if pattern and pattern != "NONE":
                signals.append(f"pattern_{pattern.lower()}")

        tracker = self._load()
        for sig in signals:
            key = f"{sig}_{sector}"
            bucket = tracker.setdefault(key, {"wins": 0, "total": 0})
            bucket["total"] += 1
            if won:
                bucket["wins"] += 1
        self._save(tracker)

    def get_best_signals(self, sector: str | None = None) -> str:
        tracker = self._load()
        results = []
        for key, stats in tracker.items():
            if stats["total"] >= 3:
                wr = stats["wins"] / stats["total"]
                if not sector or sector.lower() in key.lower():
                    results.append((wr, key, stats["total"]))

        results.sort(reverse=True)
        if not results:
            return ""
        lines = ["SIGNAL ACCURACY (from your trades):"]
        for wr, signal, n in results[:5]:
            lines.append(f"  {signal}: {wr:.0%} win rate ({n} trades)")
        return "\n".join(lines)
