"""Zero-trade regimes/strategies (BULL_TRENDING never seen yet, a strategy
with 0 closed trades, etc.) previously rendered as bare "0%" — indistinguishable
from a real 0% win rate on real trades, reading as "the system is failing."
The strategy-performance table and engine scorecard now render
"— (not seen yet)" when the trade count is 0, keeping the true win-rate
percentage for anything with real trades."""
from __future__ import annotations

from pathlib import Path

_HTML = Path("dashboard/dashboard.html").read_text(encoding="utf-8")


def test_strategy_table_shows_not_seen_yet_for_zero_trades():
    assert "not seen yet" in _HTML
    idx = _HTML.rfind("strategy-perf-tbody")
    section = _HTML[idx: idx + 1500]
    assert "trades>0?" in section


def test_engine_scorecard_shows_not_seen_yet_for_zero_trades():
    idx = _HTML.rfind("engine-scorecard")
    section = _HTML[idx: idx + 2000]
    assert "not seen yet" in section
    assert "nTrades>0?" in section


def test_mission_summary_line_present():
    assert 'id="mission-summary-line"' in _HTML
    assert "real decision" in _HTML
    assert "practice trade" in _HTML
