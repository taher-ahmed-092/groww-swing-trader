"""
Corporate action checker — bonus / split / dividend events that require stop
adjustment. Best-effort NSE public API with 24h cache; never raises.
"""
from __future__ import annotations

import json
import logging
import os
import time

import httpx

log = logging.getLogger(__name__)

_CACHE_DIR = os.path.join("data", "cache")
_TTL = 24 * 3600
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"}


class CorporateActionChecker:
    def __init__(self) -> None:
        os.makedirs(_CACHE_DIR, exist_ok=True)

    def get_upcoming_actions(self, symbol: str) -> list[dict]:
        cache = os.path.join(_CACHE_DIR, f"{symbol}_corp_actions.json")
        if os.path.exists(cache) and (time.time() - os.path.getmtime(cache)) < _TTL:
            try:
                with open(cache, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                pass
        actions: list[dict] = []
        try:
            url = f"https://www.nseindia.com/api/corporates-corporateActions?index=equities&symbol={symbol}"
            with httpx.Client(headers=_HEADERS, timeout=10, follow_redirects=True) as c:
                try:
                    c.get("https://www.nseindia.com", timeout=10)
                except Exception:
                    pass
                resp = c.get(url)
            rows = resp.json() if resp.status_code == 200 else []
            for r in (rows if isinstance(rows, list) else [])[:10]:
                actions.append({"subject": r.get("subject", ""), "exDate": r.get("exDate", "")})
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(actions, f)
        except Exception as exc:
            log.debug("corp actions fetch failed for %s: %s", symbol, exc)
        return actions

    def adjust_stop_for_corporate_action(self, stop_price: float, action: dict) -> float:
        """Adjust a stop for a price-changing action (bonus/split halve roughly)."""
        subject = (action.get("subject") or "").lower()
        if "bonus" in subject and "1:1" in subject:
            return round(stop_price / 2, 2)
        if "split" in subject and ("1:2" in subject or "1:5" in subject or "1:10" in subject):
            return round(stop_price / 2, 2)  # conservative halve for any split
        return stop_price  # dividends etc. — unchanged

    def check_stop_needs_adjustment(self, trade) -> bool:
        for a in self.get_upcoming_actions(getattr(trade, "symbol", "")):
            s = (a.get("subject") or "").lower()
            if "bonus" in s or "split" in s:
                return True
        return False
