"""get_strategy_breakdown() must sum to exactly the same total as the
dashboard/telegram scoreboard on identical input — both now read
load_all_trade_history(journal) + the same current-era filter. Previously
get_strategy_breakdown summed across ALL eras (pre+post fix) while the
scoreboard total was current-era only, so the two never matched."""
from __future__ import annotations

from datetime import datetime, timedelta

from src.analytics.era import get_era_start, split_by_era
from src.analytics.performance import PerformanceAnalyzer


def _trade(source, outcome, pnl_pct, strategy, closed_at):
    return {
        "symbol": "TEST", "pnl": pnl_pct, "pnl_pct": pnl_pct, "gross_pnl": pnl_pct,
        "outcome": outcome, "source": source, "closed_at": closed_at,
        "entry": 100, "stop": 95, "strategy": strategy,
    }


def test_strategy_breakdown_sum_equals_scoreboard_total(monkeypatch):
    era_start_dt = datetime.fromisoformat(get_era_start())
    after = (era_start_dt + timedelta(hours=1)).isoformat()
    before = (era_start_dt - timedelta(days=365)).isoformat()
    # Mix of pre-era (must be excluded) and current-era (must be counted)
    # trades across all four strategy tags plus an untagged one.
    trades = [
        _trade("cont_sim", "WIN", 2.0, "momentum", before),
        _trade("forced", "LOSS", -1.0, "mean_reversion", before),
        _trade("cont_sim", "WIN", 3.0, "momentum", after),
        _trade("cont_sim", "LOSS", -2.0, "breakout", after),
        _trade("forced", "WIN", 4.0, "pairs_trading", after),
        _trade("intraday", "LOSS", -1.5, "mean_reversion", after),
    ]
    monkeypatch.setattr(
        "src.analytics.trade_loader.load_all_trade_history", lambda journal=None: trades)

    pa = PerformanceAnalyzer()
    breakdown = pa.get_strategy_breakdown()
    strategy_total = sum(v["trades"] for v in breakdown.values())

    current_era, _all_time = split_by_era(trades)
    scoreboard_total = len(current_era)

    assert strategy_total == scoreboard_total
    assert scoreboard_total == 4  # the 2 pre-era trades are excluded from both
