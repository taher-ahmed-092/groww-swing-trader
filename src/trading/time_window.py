"""
Optimal entry-window computation for NSE swing trades.

Picks the best intraday window to enter an approved setup and produces a live
countdown. Better timing = better fills. Avoids the volatile open (9:15-9:30) and
the closing rush (after 15:00). Uses stdlib zoneinfo for IST.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class TradingTimeWindow:
    MARKET_OPEN = time(9, 15)
    MARKET_CLOSE = time(15, 30)
    AVOID_OPEN = time(9, 30)
    AVOID_CLOSE = time(15, 0)

    def compute_entry_window(self, technical_verdict: dict, state: dict) -> dict:
        indicators = technical_verdict.get("indicators", {}) or {}
        rsi = indicators.get("rsi_14") or 50
        adx = indicators.get("adx_signal", "NEUTRAL")
        pattern = indicators.get("candlestick_pattern", "NONE")
        pattern_conf = indicators.get("candlestick_confidence", "NONE")

        now_ist = datetime.now(IST)
        today_open = now_ist.replace(hour=9, minute=30, second=0, microsecond=0)
        tomorrow_open = today_open + timedelta(days=1)

        if adx == "TRENDING" and rsi > 55:
            window_open, window_close = time(9, 30), time(10, 30)
            window_label = "MORNING_MOMENTUM"
            rationale = "Strong trend — enter early to ride full move. ADX trending."
            urgency = "HIGH"
        elif pattern_conf == "HIGH" and rsi < 55:
            window_open, window_close = time(9, 45), time(11, 0)
            window_label = "REVERSAL_CONFIRM"
            rationale = f"Reversal pattern ({pattern}) needs 30-min confirmation."
            urgency = "MEDIUM"
        elif 48 < rsi < 58:
            window_open, window_close = time(9, 30), time(14, 0)
            window_label = "FLEXIBLE_ENTRY"
            rationale = "Moderate setup — flexible entry window."
            urgency = "LOW"
        else:
            window_open, window_close = time(9, 30), time(13, 0)
            window_label = "STANDARD"
            rationale = "Standard entry window."
            urgency = "MEDIUM"

        now_time = now_ist.time()
        minutes_remaining = minutes_until_open = None

        if now_time < window_open and now_time < self.MARKET_OPEN:
            open_dt = now_ist.replace(hour=window_open.hour, minute=window_open.minute,
                                      second=0, microsecond=0)
            mins = int((open_dt - now_ist).total_seconds() / 60)
            status, minutes_until_open = "WAITING", mins
            countdown = f"📅 Market opens — entry window in {mins // 60}h {mins % 60}m"
        elif now_time < window_open:
            open_dt = now_ist.replace(hour=window_open.hour, minute=window_open.minute,
                                      second=0, microsecond=0)
            mins = int((open_dt - now_ist).total_seconds() / 60)
            status, minutes_until_open = "WAITING", mins
            countdown = f"⏳ Entry window opens in {mins} min at {window_open.strftime('%H:%M')}"
        elif window_open <= now_time <= window_close:
            close_dt = now_ist.replace(hour=window_close.hour, minute=window_close.minute,
                                       second=0, microsecond=0)
            mins = int((close_dt - now_ist).total_seconds() / 60)
            status, minutes_remaining = "OPEN", mins
            countdown = (f"⏰ Entry window OPEN — {mins} min remaining" if mins > 30
                         else f"🚨 CLOSING SOON — {mins} min left to enter!")
        else:
            mins = int((tomorrow_open - now_ist).total_seconds() / 60)
            status, minutes_until_open = "MISSED", mins
            countdown = (f"❌ Today's window closed. Next: tomorrow "
                         f"{window_open.strftime('%H:%M')} IST")

        duration = (datetime.combine(date.today(), window_close)
                    - datetime.combine(date.today(), window_open)).seconds // 60

        return {
            "window_open": window_open.strftime("%H:%M"),
            "window_close": window_close.strftime("%H:%M"),
            "window_label": window_label,
            "window_duration_min": duration,
            "rationale": rationale,
            "current_status": status,
            "minutes_remaining": minutes_remaining,
            "minutes_until_open": minutes_until_open,
            "urgency": urgency,
            "auto_cancel_if_missed": True,
            "countdown_display": countdown,
        }
