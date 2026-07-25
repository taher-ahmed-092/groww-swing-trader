"""
Era segmentation — the multi-day-hold fix (commit f262968) replaced a
structurally broken engine (90-min exits vs 6%+ targets, inverted realized
R:R). Trades from before that fix reflect a different, broken system and must
not be averaged into headline metrics with genuine post-fix evidence.

data/cache/era_marker.json is written once, on first read after this fix
shipped, and is never overwritten afterward — so the marker stays pinned to
the actual deploy moment across restarts.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ERA_MARKER_FILE = Path("data/cache/era_marker.json")
ERA_FIX_COMMIT = "f262968"


def get_era_start() -> str:
    """ISO timestamp marking the start of the current (post-fix) era."""
    if ERA_MARKER_FILE.exists():
        try:
            return json.loads(ERA_MARKER_FILE.read_text())["era_start"]
        except Exception:
            pass
    era_start = datetime.now(IST).isoformat()
    try:
        ERA_MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        ERA_MARKER_FILE.write_text(json.dumps(
            {"era_start": era_start, "reason": f"multi-day holds fix {ERA_FIX_COMMIT}"}))
    except OSError:
        pass
    return era_start


def split_by_era(trades: list[dict],
                  ts_keys: tuple[str, ...] = ("closed_at", "opened_at")) -> tuple[list[dict], list[dict]]:
    """Returns (current_era, all_time). all_time is always the full input list;
    current_era is the subset at/after era_start. A trade with no timestamp in
    any of ts_keys is treated as current-era by default (most legacy/test
    records predate timestamp tagging — excluding them would silently zero out
    evidence rather than segment it)."""
    era_start = get_era_start()
    current = []
    for t in trades:
        ts = next((t.get(k) for k in ts_keys if t.get(k)), None)
        if ts is None or str(ts) >= era_start:
            current.append(t)
    return current, trades
