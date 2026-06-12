"""Visual library — pure rendering helpers, no side effects."""
from __future__ import annotations

from src.utils import visual


def test_confidence_gauge_high():
    assert "green" in visual.confidence_gauge(8.5)


def test_confidence_gauge_low():
    assert "red" in visual.confidence_gauge(4.0)


def test_mini_bar_full():
    bar = visual.mini_bar(1.0)
    assert "░" not in bar and bar.count("▓") == 5


def test_mini_bar_empty():
    bar = visual.mini_bar(0.0)
    assert "▓" not in bar and bar.count("░") == 5


def test_pnl_sparkline_doesnt_crash():
    visual.print_pnl_sparkline([])          # empty → no exception
    visual.print_pnl_sparkline([1.0, -2.0, 3.5])  # populated → no exception
