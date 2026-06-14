"""
NSE market calendar — weekends + hardcoded trading holidays (2025-2026).

Pure date logic, no network. Used to skip jobs on closed days and to flag F&O
expiry weeks (where volatility spikes). Holiday lists are public NSE schedules;
update them yearly.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

# NSE trading holidays (equity segment). Public schedule; verify/update yearly.
HOLIDAYS_2025 = {
    "2025-02-26", "2025-03-14", "2025-03-31", "2025-04-10", "2025-04-14",
    "2025-04-18", "2025-05-01", "2025-08-15", "2025-08-27", "2025-10-02",
    "2025-10-21", "2025-10-22", "2025-11-05", "2025-12-25",
}
HOLIDAYS_2026 = {
    "2026-01-26", "2026-02-15", "2026-03-04", "2026-03-21", "2026-04-01",
    "2026-04-03", "2026-04-14", "2026-05-01", "2026-08-15", "2026-10-02",
    "2026-11-09", "2026-12-25",
}
_ALL_HOLIDAYS = HOLIDAYS_2025 | HOLIDAYS_2026


class NSECalendar:
    def is_holiday(self, d: date) -> bool:
        return d.isoformat() in _ALL_HOLIDAYS

    def is_market_open(self, d: date | None = None) -> bool:
        d = d or datetime.now().date()
        if d.weekday() >= 5:  # Sat/Sun
            return False
        return not self.is_holiday(d)

    def next_trading_day(self, d: date | None = None) -> date:
        d = (d or datetime.now().date()) + timedelta(days=1)
        while not self.is_market_open(d):
            d += timedelta(days=1)
        return d

    def _last_thursday(self, year: int, month: int) -> date:
        if month == 12:
            nxt = date(year + 1, 1, 1)
        else:
            nxt = date(year, month + 1, 1)
        d = nxt - timedelta(days=1)
        while d.weekday() != 3:  # Thursday
            d -= timedelta(days=1)
        return d

    def is_fo_expiry_week(self, d: date | None = None) -> bool:
        """True in the last 3 trading days up to (and incl.) the monthly expiry."""
        d = d or datetime.now().date()
        expiry = self._last_thursday(d.year, d.month)
        # Walk back 2 trading days from expiry to get the window start.
        start = expiry
        for _ in range(2):
            start -= timedelta(days=1)
            while not self.is_market_open(start):
                start -= timedelta(days=1)
        return start <= d <= expiry

    def get_upcoming_events(self, d: date | None = None) -> list[str]:
        d = d or datetime.now().date()
        events = []
        if self.is_fo_expiry_week(d):
            events.append("F&O expiry week — expect higher volatility")
        nh = next((h for h in sorted(_ALL_HOLIDAYS) if h > d.isoformat()), None)
        if nh:
            events.append(f"Next market holiday: {nh}")
        return events
