"""
Engine Scorecard — the missing meta-learning layer.

AdaptiveThresholds adapts at the pattern/regime level, but nothing previously
adjusted the system's own learning ENGINES when they lose money. This module
tracks per-engine profitability (LIVE_FORCED, HISTORICAL_SIM, CONTINUOUS_SIM,
SHORT_SIM) over their last 100 closed trades and auto-throttles or pauses an
engine that's consistently losing — the system adjusting its own behavior,
not just its pattern confidences.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger(__name__)

THROTTLE_FILE = Path("data/cache/engine_throttle.json")
ADAPTATION_LOG_FILE = Path("data/cache/adaptation_log.json")

MIN_TRADES_FOR_THROTTLE = 50
PF_PAUSE_THRESHOLD = 0.5
PF_THROTTLE_THRESHOLD = 0.8
PF_RESTORE_THRESHOLD = 1.2
# Below this many CURRENT-ERA trades, an engine is still proving itself post-fix
# — "probation" (half-size entries) lets evidence accumulate instead of either
# trading it silently at full size or leaving it stuck "paused" on dead
# pre-fix-era data forever.
PROBATION_MIN_TRADES = 20

# Each engine's history source + how to identify its trades within that file.
_ENGINE_SOURCES = {
    "LIVE_FORCED": ("data/cache/forced_trades_history.json",
                    lambda t: t.get("trade_type") in ("LIVE_FORCED", "RANDOM_FORCED")),
    "HISTORICAL_SIM": ("data/cache/forced_trades_history.json",
                       lambda t: t.get("trade_type") == "HISTORICAL_SIM"),
    "CONTINUOUS_SIM": ("data/cache/continuous_sim_history.json", lambda t: True),
    "SHORT_SIM": ("data/cache/short_trades_history.json", lambda t: True),
}


def log_adaptation(entry_type: str, detail: str, evidence: str) -> None:
    """Shared adaptation-log writer — makes every self-adjustment visible,
    whatever adjusted it (thresholds, drift decay, or engine throttling)."""
    history = []
    if ADAPTATION_LOG_FILE.exists():
        try:
            history = json.loads(ADAPTATION_LOG_FILE.read_text())
        except Exception:
            pass
    history.append({
        "ts": datetime.now(IST).isoformat(),
        "type": entry_type,
        "detail": detail,
        "evidence": evidence,
    })
    try:
        ADAPTATION_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ADAPTATION_LOG_FILE.write_text(json.dumps(history[-200:]))
    except OSError:
        pass


class EngineScorecard:
    def compute(self) -> dict:
        """Per-engine {win_rate, avg_win, avg_loss, profit_factor, expectancy,
        n_trades} over each engine's last 100 CURRENT-ERA (post-f262968)
        closed trades — pre-fix trades came from a structurally broken engine
        (90-min holds vs 6%+ targets) and must not dilute the read on whether
        the fixed engine is actually working."""
        from src.analytics.era import split_by_era

        scorecard: dict = {}
        for engine, (fname, matcher) in _ENGINE_SOURCES.items():
            trades = self._load_matching(fname, matcher)
            current_era, _all_time = split_by_era(trades)
            scorecard[engine] = self._score(current_era[-100:])
        return scorecard

    def apply_throttles(self) -> dict:
        """Writes engine_throttle.json based on compute() (current-era only);
        returns only the engines whose mode actually changed this run, logging
        each change. Below PROBATION_MIN_TRADES current-era trades, an engine
        goes to "probation" regardless of any stale pre-fix "paused" state —
        otherwise a correctly-fixed engine could stay paused forever on dead
        pre-fix data and never get the chance to prove itself with new evidence."""
        scorecard = self.compute()
        current = self._load_throttles()
        changes: dict = {}

        for engine, stats in scorecard.items():
            n = stats["n_trades"]
            pf = stats["profit_factor"]
            prev_mode = current.get(engine, {}).get("mode", "normal")

            if n < PROBATION_MIN_TRADES:
                new_mode = "probation"
                reason = (f"current-era evidence still building ({n}/{PROBATION_MIN_TRADES} "
                          "trades) — half-size entries while proving itself")
            elif n < MIN_TRADES_FOR_THROTTLE:
                new_mode = "probation" if prev_mode == "paused" else prev_mode
                reason = f"insufficient evidence ({n}/{MIN_TRADES_FOR_THROTTLE} trades)"
            elif pf < PF_PAUSE_THRESHOLD:
                new_mode, reason = "paused", f"PF {pf:.2f} < {PF_PAUSE_THRESHOLD} over {n} trades"
            elif pf < PF_THROTTLE_THRESHOLD:
                new_mode, reason = "throttled", f"PF {pf:.2f} < {PF_THROTTLE_THRESHOLD} over {n} trades"
            elif pf > PF_RESTORE_THRESHOLD:
                new_mode, reason = "normal", f"PF {pf:.2f} > {PF_RESTORE_THRESHOLD} over {n} trades"
            else:
                new_mode, reason = prev_mode, f"PF {pf:.2f} in neutral band over {n} trades"

            current[engine] = {
                "mode": new_mode, "pf": pf,
                "updated": datetime.now(IST).isoformat(), "reason": reason,
            }
            if new_mode != prev_mode:
                changes[engine] = {"old": prev_mode, "new": new_mode, "pf": pf, "reason": reason}
                log_adaptation(
                    "throttle",
                    f"{engine}: {prev_mode} -> {new_mode} ({reason})",
                    f"PF={pf:.2f}, n_trades={n}, win_rate={stats['win_rate']:.0%}")

        self._save_throttles(current)
        return changes

    @staticmethod
    def get_mode(engine: str) -> str:
        """normal | throttled | paused — read by entry points before trading."""
        if not THROTTLE_FILE.exists():
            return "normal"
        try:
            data = json.loads(THROTTLE_FILE.read_text())
            return data.get(engine, {}).get("mode", "normal")
        except Exception:
            return "normal"

    # ── internals ────────────────────────────────────────────────────────────
    @staticmethod
    def _load_matching(fname: str, matcher) -> list[dict]:
        p = Path(fname)
        if not p.exists():
            return []
        try:
            trades = json.loads(p.read_text())
        except Exception:
            return []
        return [t for t in trades if t.get("outcome") in ("WIN", "LOSS") and matcher(t)]

    @staticmethod
    def _score(trades: list[dict]) -> dict:
        n = len(trades)
        if n == 0:
            return {"win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                    "profit_factor": 0.0, "expectancy": 0.0, "n_trades": 0}
        pnls = [t.get("pnl_pct", 0) or 0 for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        win_rate = len(wins) / n
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        gross_wins = sum(wins)
        gross_losses = abs(sum(losses))
        profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else 999.99
        expectancy = round(win_rate * avg_win + (1 - win_rate) * avg_loss, 3)
        return {
            "win_rate": round(win_rate, 3), "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2), "profit_factor": profit_factor,
            "expectancy": expectancy, "n_trades": n,
        }

    @staticmethod
    def _load_throttles() -> dict:
        if THROTTLE_FILE.exists():
            try:
                return json.loads(THROTTLE_FILE.read_text())
            except Exception:
                pass
        return {}

    @staticmethod
    def _save_throttles(data: dict) -> None:
        try:
            THROTTLE_FILE.parent.mkdir(parents=True, exist_ok=True)
            THROTTLE_FILE.write_text(json.dumps(data, indent=2))
        except OSError:
            pass
