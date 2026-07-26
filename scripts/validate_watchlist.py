"""Validates every symbol in src/data/watchlist.py against yfinance.

Fetches a 5-day history per symbol (rate-limit friendly sleep between calls)
and reports any that return no data — a strong signal the ticker was
delisted, renamed, or is otherwise unresolvable on NSE.

Usage: uv run python scripts/validate_watchlist.py

Also invoked monthly by runner.py's watchlist_validation_job, which diffs the
dead-ticker list against the previous run (data/cache/dead_tickers.json) and
Telegram-reports only NEWLY dead tickers so the scheduled job doesn't spam
the same known-quarantined symbols every month.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import yfinance as yf

from src.data.watchlist import ALL_STOCKS

SLEEP_SECONDS = 0.5
DEAD_TICKERS_FILE = Path("data/cache/dead_tickers.json")


def validate() -> list[str]:
    dead: list[str] = []
    total = len(ALL_STOCKS)
    for i, symbol in enumerate(sorted(ALL_STOCKS), start=1):
        ticker = f"{symbol}.NS"
        try:
            df = yf.Ticker(ticker).history(period="5d")
            ok = df is not None and not df.empty
        except Exception as exc:
            ok = False
            print(f"[{i}/{total}] {symbol}: ERROR {exc}")
        if not ok:
            dead.append(symbol)
            print(f"[{i}/{total}] {symbol}: NO DATA")
        else:
            print(f"[{i}/{total}] {symbol}: ok")
        time.sleep(SLEEP_SECONDS)

    print("\n--- dead tickers ---")
    for s in dead:
        print(s)
    print(f"\n{len(dead)}/{total} symbols returned no data")
    return dead


def validate_and_report_new_dead_tickers() -> list[str]:
    """Runs validate(), diffs against the previously recorded dead-ticker set,
    persists the new set, and returns only the NEWLY dead tickers (empty if
    none, or if this is the first run — nothing to diff against yet)."""
    previous: list[str] = []
    if DEAD_TICKERS_FILE.exists():
        try:
            previous = json.loads(DEAD_TICKERS_FILE.read_text())
        except Exception:
            previous = []

    current = validate()

    try:
        DEAD_TICKERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEAD_TICKERS_FILE.write_text(json.dumps(sorted(current)))
    except OSError:
        pass

    if not previous:
        return []  # first run — no baseline to diff against
    return sorted(set(current) - set(previous))


if __name__ == "__main__":
    validate()
