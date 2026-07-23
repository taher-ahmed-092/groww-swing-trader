"""Professional analytics — profit factor, max drawdown, strategy decay."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.analytics.performance import PerformanceAnalyzer


def _fake_trades(pnls):
    return [{"pnl": p, "outcome": "WIN" if p > 0 else "LOSS"} for p in pnls]


def test_profit_factor_computed():
    pa = PerformanceAnalyzer(journal=MagicMock())
    with patch("src.analytics.trade_loader.load_all_trade_history",
               return_value=_fake_trades([2, 2, -1, -1])):
        metrics = pa.get_professional_metrics()
    assert metrics["profit_factor"] == 2.0


def test_max_drawdown_positive():
    pa = PerformanceAnalyzer(journal=MagicMock())
    with patch("src.analytics.trade_loader.load_all_trade_history",
               return_value=_fake_trades([2, -5, 1])):
        metrics = pa.get_professional_metrics()
    assert metrics["max_drawdown_pct"] > 0


def test_decay_alert_fires_on_wr_drop():
    pa = PerformanceAnalyzer(journal=MagicMock())
    prev20 = [1] * 16 + [-1] * 4  # 80% WR
    recent20 = [1] * 4 + [-1] * 16  # 20% WR
    with patch("src.analytics.trade_loader.load_all_trade_history",
               return_value=_fake_trades(prev20 + recent20)):
        metrics = pa.get_professional_metrics()
    assert metrics["decay_alert"] is True
    assert metrics["trend"] == "DECAYING"


def test_no_data_safe():
    pa = PerformanceAnalyzer(journal=MagicMock())
    with patch("src.analytics.trade_loader.load_all_trade_history", return_value=[]):
        metrics = pa.get_professional_metrics()
    assert metrics["status"] == "no_data"
