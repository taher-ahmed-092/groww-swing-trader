"""The Flight Log panel (Mission Control's mini scrolling trade list) showed
nothing useful — the plane animations already carry the same information
visually. Removed entirely: CSS classes, the panel markup, and the JS that
fed it. The plane-animation code (launchTradePlane/_launchSinglePlane/etc.)
and the source-color legend stay — those are unrelated, working features."""
from __future__ import annotations

from pathlib import Path

_HTML = Path("dashboard/dashboard.html").read_text(encoding="utf-8")


def test_flight_log_panel_and_css_absent():
    assert "flight-log" not in _HTML
    assert "flight-log__title" not in _HTML
    assert "Flight Log</div>" not in _HTML


def test_flight_log_js_absent():
    assert "_flightLog" not in _HTML
    assert "addFlightLogEntry" not in _HTML


def test_plane_animation_and_legend_still_present():
    assert "launchTradePlane" in _HTML
    assert "_launchSinglePlane" in _HTML
    assert "legend-row" in _HTML
