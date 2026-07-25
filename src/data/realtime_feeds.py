"""
Real-time data feeds that add genuine edge beyond end-of-day prices.

1. NSE Pre-open (9:00-9:15 AM): order imbalance = directional signal
2. FII/DII flows (5:30 PM): institutional money direction for next day
3. Economic calendar: know before trading when RBI/CPI/GDP releases land
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

IST = ZoneInfo("Asia/Kolkata")
CACHE_DIR = Path("data/cache/realtime")

_NSE_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"}


class NSEPreOpenFeed:
    """NSE pre-open session data (9:00-9:15 AM IST). Strong buy imbalance
    often predicts a positive gap-up or continuation."""

    def get_preopen_data(self) -> dict:
        """Returns {symbol: {buy_qty, sell_qty, imbalance_pct}}."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = CACHE_DIR / f"preopen_{date.today()}.json"
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text())
            except Exception:
                pass

        now = datetime.now(IST)
        if not (9 <= now.hour < 10):
            return {}  # only fetch during the pre-open window

        try:
            with requests.Session() as s:
                s.headers.update(_NSE_HEADERS)
                try:
                    s.get("https://www.nseindia.com", timeout=10)
                except Exception:
                    pass
                r = s.get("https://www.nseindia.com/api/market-data-pre-open?key=NIFTY", timeout=10)
            if r.status_code != 200:
                return {}
            data = r.json().get("data", [])
            result = {}
            for item in data:
                sym = item.get("metadata", {}).get("symbol", "")
                detail = item.get("detail", {})
                buy_qty = detail.get("buyQuantity", 0)
                sell_qty = detail.get("sellQuantity", 0)
                if buy_qty + sell_qty > 0:
                    imbalance = (buy_qty - sell_qty) / (buy_qty + sell_qty) * 100
                    result[sym] = {"buy_qty": buy_qty, "sell_qty": sell_qty,
                                  "imbalance_pct": round(imbalance, 1)}
            if result:
                cache_file.write_text(json.dumps(result))
            return result
        except Exception:
            return {}

    def get_imbalance_for_stock(self, symbol: str) -> float:
        """Returns imbalance % for a stock. Positive = buy pressure."""
        return self.get_preopen_data().get(symbol, {}).get("imbalance_pct", 0.0)


class FIIDIIFeed:
    """FII/DII provisional data — released ~5:30 PM on NSE."""

    @staticmethod
    def _last_known_file() -> Path:
        """Last-known-good fetch, persisted across days — the daily cache_file
        above is per-calendar-day and empty on weekends/failures; this file
        survives those gaps so callers can show "last known value (as of
        <date>)" instead of a fetch failure silently rendering as a real
        "+0Cr" data point (audit finding: indistinguishable from a genuine
        zero-flow day). Resolved against the module-level CACHE_DIR at call
        time (not frozen as a class attribute) so test monkeypatching of
        CACHE_DIR is honored."""
        return CACHE_DIR / "fiidii_last_known.json"

    def _fetch_today(self) -> dict:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = CACHE_DIR / f"fiidii_{date.today()}.json"
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text())
            except Exception:
                pass
        try:
            with requests.Session() as s:
                s.headers.update(_NSE_HEADERS)
                try:
                    s.get("https://www.nseindia.com", timeout=10)
                except Exception:
                    pass
                r = s.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=10)
            if r.status_code != 200:
                return {}
            data = r.json()
            result: dict = {}
            for item in (data if isinstance(data, list) else []):
                cat = str(item.get("category", ""))
                net = item.get("netPurchaseSales", 0)
                if "FII" in cat.upper():
                    result["fii_net_crore"] = round(net, 2)
                elif "DII" in cat.upper():
                    result["dii_net_crore"] = round(net, 2)

            if not result:
                return {}

            fii = result.get("fii_net_crore", 0)
            if fii > 500:
                result["signal"] = "BULLISH"
                result["signal_reason"] = f"FIIs bought ₹{fii:.0f}Cr — institutional inflow supports market"
            elif fii < -500:
                result["signal"] = "BEARISH"
                result["signal_reason"] = f"FIIs sold ₹{abs(fii):.0f}Cr — outflow creates headwind"
            else:
                result["signal"] = "NEUTRAL"
                result["signal_reason"] = "Modest FII activity"

            cache_file.write_text(json.dumps(result))
            return result
        except Exception:
            return {}

    def get_latest(self) -> dict:
        """Returns today's FII/DII flows if fetched successfully. On weekend/
        failure, falls back to the last successfully-fetched value tagged with
        its date ("as_of" + "stale": True) rather than a bare {} that the
        caller could mistake for a genuine zero. Returns {"unavailable": True}
        only if nothing has EVER been fetched successfully."""
        last_known_file = self._last_known_file()
        result = self._fetch_today()
        if result:
            fresh = {**result, "as_of": date.today().isoformat(), "stale": False}
            try:
                last_known_file.parent.mkdir(parents=True, exist_ok=True)
                last_known_file.write_text(json.dumps(fresh))
            except OSError:
                pass
            return fresh

        if last_known_file.exists():
            try:
                last_known = json.loads(last_known_file.read_text())
                return {**last_known, "stale": True}
            except Exception:
                pass

        return {"unavailable": True}


class EconomicCalendar:
    """Key Indian economic events that move markets."""

    EVENTS_2026 = [
        {"date": "2026-08-06", "event": "RBI MPC Decision", "impact": "HIGH"},
        {"date": "2026-10-08", "event": "RBI MPC Decision", "impact": "HIGH"},
        {"date": "2026-12-04", "event": "RBI MPC Decision", "impact": "HIGH"},
        {"date": "2026-07-31", "event": "GDP Q1 FY27 Data", "impact": "HIGH"},
        {"date": "2026-08-12", "event": "CPI Inflation Data", "impact": "MEDIUM"},
        {"date": "2026-09-14", "event": "IIP + CPI Data", "impact": "MEDIUM"},
    ]

    def get_upcoming(self, days_ahead: int = 7) -> list[dict]:
        today = date.today()
        upcoming = []
        for ev in self.EVENTS_2026:
            ev_date = date.fromisoformat(ev["date"])
            diff = (ev_date - today).days
            if 0 <= diff <= days_ahead:
                upcoming.append({**ev, "days_away": diff})
        return sorted(upcoming, key=lambda x: x["days_away"])

    def should_reduce_size(self) -> tuple[bool, str]:
        """(True, reason) if a HIGH-impact event is within 2 trading days."""
        high_impact = [e for e in self.get_upcoming(days_ahead=2) if e["impact"] == "HIGH"]
        if high_impact:
            ev = high_impact[0]
            return True, f"{ev['event']} in {ev['days_away']} days — size reduced"
        return False, ""
