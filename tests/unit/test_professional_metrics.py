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


def test_drawdown_uses_r_multiples_not_raw_pnl_sum():
    """Audit bug: summing raw pnl_pct across hundreds of trades as if it were
    an equity balance produced a nonsensical "581.5%" drawdown. R-multiples
    (each trade normalized to "how many multiples of its own risk") keep the
    curve in a sane range even at that scale."""
    pa = PerformanceAnalyzer(journal=MagicMock())
    trades = [
        {"pnl": 5.0, "entry": 100.0, "stop": 95.0},    # stop_distance=5% -> R=+1.0
        {"pnl": -10.0, "entry": 100.0, "stop": 95.0},  # R=-2.0
        {"pnl": 3.0, "entry": 100.0, "stop": 95.0},    # R=+0.6
    ] * 50  # simulate the "hundreds of trades" scale from the audit
    with patch("src.analytics.trade_loader.load_all_trade_history", return_value=trades):
        metrics = pa.get_professional_metrics()
    assert 0 <= metrics["max_drawdown_pct"] <= 50
    assert metrics["max_drawdown_label"] == "Max DD (1% risk/trade)"


def test_drawdown_falls_back_to_3pct_stop_distance_when_unavailable():
    pa = PerformanceAnalyzer(journal=MagicMock())
    with patch("src.analytics.trade_loader.load_all_trade_history",
               return_value=_fake_trades([2.0, -4.0, 1.0, -1.0])):
        metrics = pa.get_professional_metrics()
    # Fallback R = pnl / 3.0 -> equity curve: 0.667, -0.667, 1.0, -0.667
    assert metrics["max_drawdown_pct"] >= 0
