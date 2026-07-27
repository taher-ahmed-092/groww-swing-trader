"""Stale-symbol pruning for replay_progress.json.

Renamed/removed watchlist tickers (e.g. ZOMATO -> ETERNAL, GSPL removed) left
stale keys behind in this cache, inflating reported replay coverage past
100% and letting dead tickers monopolize batch selection forever.
"""
from __future__ import annotations

import json

import src.learning.historical_replay as hr_mod
from src.data.watchlist import ALL_STOCKS
from src.learning.historical_replay import HistoricalReplayEngine


def test_prunes_stale_symbols_and_persists(tmp_path, monkeypatch):
    cache = tmp_path / "replay_progress.json"
    live_symbol = next(iter(ALL_STOCKS))
    cache.write_text(json.dumps({live_symbol: "2026-01-01", "ZOMATO": "2025-01-01",
                                  "GSPL": "2025-01-01"}))
    monkeypatch.setattr(hr_mod, "REPLAY_CACHE", cache)

    progress = HistoricalReplayEngine()._load_progress()

    assert live_symbol in progress
    assert "ZOMATO" not in progress
    assert "GSPL" not in progress
    assert json.loads(cache.read_text()) == progress  # pruning persists to disk


def test_coverage_never_exceeds_watchlist_total(tmp_path, monkeypatch):
    cache = tmp_path / "replay_progress.json"
    cache.write_text(json.dumps({sym: "2026-01-01" for sym in ALL_STOCKS}))
    monkeypatch.setattr(hr_mod, "REPLAY_CACHE", cache)

    stats = HistoricalReplayEngine().get_stats()
    assert stats["total_stocks_replayed"] <= len(ALL_STOCKS)
    assert stats["stocks_pending"] >= 0
