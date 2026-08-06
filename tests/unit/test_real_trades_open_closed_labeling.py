""""Real Pipeline Trades: Total: 0" was misleading whenever open positions
(e.g. OFSS, TANLA) exist but haven't closed yet — a bare 0 read as "nothing
happened" despite real capital deployed. /report and the dashboard now show
"{open} open, {closed} closed (WwLl)" instead of a closed-only total."""
from __future__ import annotations

from pathlib import Path

_CMD_SRC = Path("src/notifications/command_handler.py").read_text(encoding="utf-8")
_HTML = Path("dashboard/dashboard.html").read_text(encoding="utf-8")
_SERVER_SRC = Path("dashboard/server.py").read_text(encoding="utf-8")


def test_report_no_longer_shows_bare_total_label():
    assert "Real Pipeline Trades" not in _CMD_SRC
    assert "*📈 Real Trades*" in _CMD_SRC


def test_report_computes_open_and_closed_counts():
    assert "get_open_trades()" in _CMD_SRC
    assert "get_closed_trades()" in _CMD_SRC
    idx = _CMD_SRC.find("*📈 Real Trades*")
    section = _CMD_SRC[idx: idx + 300]
    assert "open_real" in section and "closed_real" in section


def test_journal_exposes_closed_trades_helper():
    src = Path("src/memory/journal.py").read_text(encoding="utf-8")
    assert "def get_closed_trades" in src


def test_dashboard_summary_carries_open_trades_count():
    assert '"open_trades": len(open_trades)' in _SERVER_SRC


def test_dashboard_real_trades_tile_shows_open_and_closed():
    assert "Real pipeline trades" not in _HTML
    idx = _HTML.rfind("metric-trades")
    section = _HTML[idx: idx + 400]
    assert "open_trades" in section or "openN" in section
