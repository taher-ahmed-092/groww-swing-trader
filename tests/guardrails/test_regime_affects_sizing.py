"""Regime multiplier must shrink position size in bear/volatile regimes."""
from __future__ import annotations

from src.risk.position_sizer import PositionSizer


class _StubJournal:
    def get_performance_summary(self) -> dict:
        return {"total_closed": 10, "win_rate": 0.6, "avg_win_pct": 0,
                "avg_loss_pct": 0, "consecutive_wins": 0, "consecutive_losses": 0}


def _state(size_mult):
    return {
        "judge_verdict": {"overall_score": 7.0},
        "technical_verdict": {"entry_price": 100, "stop_price": 90, "target_price": 120},
        "market_context": {"size_multiplier": size_mult},
    }


def test_bear_regime_halves_position():
    sizer = PositionSizer(journal=_StubJournal())
    bull = sizer.calculate(_state(1.0), 1000)["position_size_inr"]
    bear = sizer.calculate(_state(0.5), 1000)["position_size_inr"]
    assert bear <= bull * 0.5 + 1e-6


def test_volatile_regime_halves_position():
    sizer = PositionSizer(journal=_StubJournal())
    base = sizer.calculate(_state(1.0), 1000)["position_size_inr"]
    volatile = sizer.calculate(_state(0.5), 1000)["position_size_inr"]
    assert volatile <= base * 0.5 + 1e-6
