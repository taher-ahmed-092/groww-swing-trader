"""NSE market calendar — weekends + holidays."""
from __future__ import annotations

from datetime import date

from src.data.market_calendar import NSECalendar


def test_weekend_closed():
    # 2026-01-24 is a Saturday.
    assert NSECalendar().is_market_open(date(2026, 1, 24)) is False


def test_holiday_closed():
    # Republic Day 2026-01-26.
    assert NSECalendar().is_market_open(date(2026, 1, 26)) is False


def test_weekday_open():
    # 2026-01-07 is a normal Wednesday (not a holiday).
    assert NSECalendar().is_market_open(date(2026, 1, 7)) is True


def test_next_trading_day_skips_weekend():
    # Friday 2026-01-02 → next is Monday 2026-01-05 (skips the weekend).
    assert NSECalendar().next_trading_day(date(2026, 1, 2)) == date(2026, 1, 5)
