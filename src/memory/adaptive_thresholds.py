"""
The missing feedback loop.

When forced/simulated trades accumulate enough evidence about what works in a
regime, this module adjusts the pipeline's judge-approval threshold automatically.

This is how the system learns like a trader, not just memorizes:
- Forced RECOVERY trades win 65% → lower judge threshold in RECOVERY
- Forced VOLATILE trades lose 80% → raise threshold in VOLATILE

Evidence threshold: need 15+ trades in a regime before adjusting.
Max adjustment: ±1.5 points on judge threshold (safe, bounded).
"""
from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
THRESHOLDS_FILE = Path("data/cache/adaptive_thresholds.json")

DEFAULT_THRESHOLDS = {
    "BULL_TRENDING": {"judge_min": 6.5, "evidence": 0},
    "RECOVERY": {"judge_min": 6.5, "evidence": 0},
    "RANGE_BOUND": {"judge_min": 6.5, "evidence": 0},
    "VOLATILE": {"judge_min": 7.5, "evidence": 0},
    "TRANSITIONAL": {"judge_min": 7.0, "evidence": 0},
    "BEAR_TRENDING": {"judge_min": 8.0, "evidence": 0},
}

MIN_EVIDENCE = 15  # need 15+ trades in a regime before adjusting

# Live forced trades are real market-hours signal quality; simulations are
# systematically more pessimistic (audit finding: live 53% vs sim 36% WR) —
# so a regime's win rate is weighted by which engine produced the evidence,
# not counted flat. intraday_sim_history.json trades map to "SHORT" (same
# same-day-resolution character as a short-hold simulation).
SOURCE_WEIGHTS = {
    "LIVE_FORCED": 1.0,
    "CONTINUOUS_SIM": 0.5,
    "HISTORICAL_SIM": 0.3,
    "SHORT": 0.4,
}


def _source_tag(fname: str, trade: dict) -> str:
    if fname.endswith("continuous_sim_history.json"):
        return "CONTINUOUS_SIM"
    if fname.endswith("forced_trades_history.json"):
        return "HISTORICAL_SIM" if trade.get("trade_type") == "HISTORICAL_SIM" else "LIVE_FORCED"
    return "SHORT"  # intraday_sim_history.json / short_trades_history.json


class AdaptiveThresholds:
    def load(self) -> dict:
        if THRESHOLDS_FILE.exists():
            try:
                return json.loads(THRESHOLDS_FILE.read_text())
            except Exception:
                pass
        return {k: dict(v) for k, v in DEFAULT_THRESHOLDS.items()}

    def save(self, thresholds: dict) -> None:
        THRESHOLDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        THRESHOLDS_FILE.write_text(json.dumps(thresholds, indent=2))

    def get_judge_threshold(self, regime: str) -> float:
        """Current adaptive judge threshold for a regime. Called by the judge
        evaluator before scoring."""
        thresholds = self.load()
        return thresholds.get(regime, {}).get("judge_min", 6.5)

    def update_from_all_trades(self) -> dict:
        """Analyzes all forced + simulated trade outcomes by regime and adjusts
        thresholds based on evidence. Returns {regime: {old, new, evidence, win_rate}}
        for regimes that actually changed. Run after each learning cycle — this
        IS the feedback loop."""
        source_files = (
            "data/cache/forced_trades_history.json",
            "data/cache/intraday_sim_history.json",
            "data/cache/short_trades_history.json",
            "data/cache/continuous_sim_history.json",
        )
        all_trades: list[tuple[str, dict]] = []
        for fname in source_files:
            p = Path(fname)
            if p.exists():
                try:
                    all_trades.extend((fname, t) for t in json.loads(p.read_text()))
                except Exception:
                    pass

        by_regime: dict = {}
        for fname, trade in all_trades:
            regime = trade.get("regime", "UNKNOWN")
            if regime in ("UNKNOWN", "ANY", "INTRADAY_SHORT", "HISTORICAL_SIM"):
                continue
            by_regime.setdefault(regime, {"weighted_wins": 0.0, "weighted_total": 0.0, "raw_total": 0})
            weight = SOURCE_WEIGHTS.get(_source_tag(fname, trade), 1.0)
            by_regime[regime]["weighted_total"] += weight
            by_regime[regime]["raw_total"] += 1
            if trade.get("outcome") == "WIN":
                by_regime[regime]["weighted_wins"] += weight

        thresholds = self.load()
        changes = {}

        for regime, stats in by_regime.items():
            # Evidence gate stays on raw trade count — weighting affects the
            # win-rate calculation, not whether we've seen "enough" trades.
            if stats["raw_total"] < MIN_EVIDENCE:
                continue
            if regime not in thresholds:
                thresholds[regime] = {"judge_min": 6.5, "evidence": 0}

            win_rate = (stats["weighted_wins"] / stats["weighted_total"]
                       if stats["weighted_total"] else 0.0)
            old_threshold = thresholds[regime]["judge_min"]

            # WR > 60%: market is cooperative → lower bar slightly.
            # WR 45-60%: neutral → keep bar.
            # WR < 45%: market is hostile → raise bar.
            if win_rate > 0.60:
                adjustment = -min(0.5 * (win_rate - 0.60) * 10, 1.5)
            elif win_rate < 0.45:
                adjustment = min(0.5 * (0.45 - win_rate) * 10, 1.5)
            else:
                adjustment = 0.0

            new_threshold = round(max(5.0, min(9.0, old_threshold + adjustment)), 2)
            thresholds[regime]["judge_min"] = new_threshold
            thresholds[regime]["evidence"] = stats["raw_total"]
            thresholds[regime]["win_rate"] = round(win_rate, 3)

            if abs(new_threshold - old_threshold) > 0.05:
                changes[regime] = {
                    "old": old_threshold,
                    "new": new_threshold,
                    "evidence": stats["raw_total"],
                    "win_rate": f"{win_rate:.0%}",
                }

        self.save(thresholds)

        if changes:
            from src.analytics.strategy_scorecard import log_adaptation

            for regime, c in changes.items():
                log_adaptation(
                    "threshold",
                    f"{regime}: judge threshold {c['old']} -> {c['new']}",
                    f"{c['evidence']} trades, {c['win_rate']} weighted win rate")

        return changes
