"""Source-weighted adaptive thresholds — live evidence outweighs sim evidence
(audit finding: live 53% vs sim 36% WR, so sims must count for less)."""
from __future__ import annotations

import json
from pathlib import Path

from src.memory.adaptive_thresholds import AdaptiveThresholds


def test_live_wins_outweigh_historical_sim_losses(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 15 real live-forced WINs (weight 1.0 each) + 20 historical-sim LOSSes
    # (weight 0.3 each) in the same regime. Unweighted WR = 15/35 = 43%
    # (neutral, no threshold change). Weighted WR = 15 / (15 + 6) = 71%
    # (clearly > 60% -> threshold lowered) — the live evidence dominates.
    trades = (
        [{"outcome": "WIN", "regime": "RECOVERY", "trade_type": "LIVE_FORCED"}] * 15
        + [{"outcome": "LOSS", "regime": "RECOVERY", "trade_type": "HISTORICAL_SIM"}] * 20
    )
    p = Path("data/cache/forced_trades_history.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(trades))

    changes = AdaptiveThresholds().update_from_all_trades()
    assert "RECOVERY" in changes
    assert changes["RECOVERY"]["new"] < changes["RECOVERY"]["old"]  # lowered (easier)
    assert changes["RECOVERY"]["win_rate"] == "71%"
