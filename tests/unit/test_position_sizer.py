"""Kelly position-sizing behavior. Uses a stub journal for deterministic win rates."""
from __future__ import annotations

from src.risk.position_sizer import PositionSizer


class _StubJournal:
    def __init__(self, total: int, win_rate: float) -> None:
        self._t, self._wr = total, win_rate

    def get_performance_summary(self) -> dict:
        return {"total_closed": self._t, "win_rate": self._wr, "avg_win_pct": 0,
                "avg_loss_pct": 0, "consecutive_wins": 0, "consecutive_losses": 0}


def _state(judge=7.0, entry=100, stop=90, target=120, size_mult=1.0):
    return {
        "judge_verdict": {"overall_score": judge},
        "technical_verdict": {"entry_price": entry, "stop_price": stop, "target_price": target},
        "market_context": {"size_multiplier": size_mult},
    }


def test_kelly_calculation_known_values():
    # W=0.6, R=2.0 → kelly=0.4 → half_kelly=0.2.
    sizer = PositionSizer(journal=_StubJournal(10, 0.6))
    out = sizer.calculate(_state(entry=100, stop=90, target=120), 1500)
    assert out["kelly_fraction"] == 0.2
    assert out["win_rate_used"] == 0.6


def test_negative_kelly_gives_minimum():
    # W=0.3, R=1.0 → kelly negative → size clamps to MIN.
    sizer = PositionSizer(journal=_StubJournal(10, 0.3))
    out = sizer.calculate(_state(entry=100, stop=90, target=110), 1500)
    assert out["position_size_inr"] == PositionSizer.MIN_TRADE_INR


def test_high_confidence_approaches_max():
    sizer = PositionSizer(journal=_StubJournal(10, 0.7))
    out = sizer.calculate(_state(judge=9.5, entry=100, stop=90, target=125), 5000)
    assert out["position_size_inr"] <= PositionSizer.MAX_TRADE_INR


def test_low_confidence_reduces_size():
    sizer = PositionSizer(journal=_StubJournal(10, 0.7))
    high = sizer.calculate(_state(judge=9.5, entry=100, stop=90, target=125), 1000)
    low = sizer.calculate(_state(judge=5.5, entry=100, stop=90, target=125), 1000)
    assert low["position_size_inr"] < high["position_size_inr"]


def test_default_winrate_used_below_threshold():
    sizer = PositionSizer(journal=_StubJournal(0, 0.9))  # 0 trades → ignore stub WR
    out = sizer.calculate(_state(), 1500)
    assert out["win_rate_used"] == PositionSizer.DEFAULT_WIN_RATE
