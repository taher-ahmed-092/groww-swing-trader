"""
Concept Drift Detector — detects when market conditions change and the
system's learned rules become stale.

An experienced trader knows when their edge is deteriorating and adjusts.
This system does the same automatically.

Three types of drift detected:
1. Win rate drift: recent WR vs prior WR drops >15 percentage points.
2. Pattern drift: a high-confidence (>=0.75) knowledge-base pattern that used
   to win is now losing consistently in recent simulations.
3. Regime drift: the bulk of recent simulation data was gathered in one
   regime, but the market has since moved to a different one.

When drift is detected: knowledge-base confidence is decayed (20%), auto-rules
are regenerated from fresh evidence, and the result is logged for audit.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.memory.journal import TradingJournal

DRIFT_HISTORY_FILE = Path("data/cache/drift_history.json")

# A drop computed from too few trades (or a "71% loss rate" that's really
# 5-of-7) is noise dressed up as a finding — these floors gate every drift
# check on genuinely sufficient evidence before declaring drift.
MIN_TRADES_PER_WINDOW = 20   # each of the two 30-trade windows needs this many
MIN_RECENT_MATCHES = 10      # pattern-drift sample floor (was 3 — too small)
MIN_REGIME_TAGGED_RECORDS = 20  # regime-drift sample floor, post market_regime filter


class DriftDetector:
    def __init__(self) -> None:
        self.journal = TradingJournal()

    def check_and_respond(self) -> dict:
        """Runs all drift checks; if any fired, decays confidence, regenerates
        auto-rules, and logs the result. Returns the full results dict plus
        `any_drift`."""
        results = {
            "wr_drift": self._check_wr_drift(),
            "pattern_drift": self._check_pattern_drift(),
            "regime_drift": self._check_regime_drift(),
            "timestamp": datetime.now().isoformat(),
        }

        any_drift = any(
            isinstance(r, dict) and r.get("detected") for r in
            (results["wr_drift"], results["pattern_drift"], results["regime_drift"])
        )

        if any_drift:
            self._apply_confidence_decay()
            try:
                from src.memory.auto_rules import AutoRuleExtractor

                AutoRuleExtractor().extract_and_save()
            except Exception:
                pass
            self._log_drift(results)

        results["any_drift"] = any_drift
        return results

    # ── checks ───────────────────────────────────────────────────────────────
    def _check_wr_drift(self) -> dict:
        """Rolling 30-vs-30 win rate comparison across all simulation sources."""
        all_trades = self._load_all_sims()
        if len(all_trades) < 60:
            return {"detected": False, "reason": "insufficient data"}

        recent = all_trades[-30:]
        previous = all_trades[-60:-30]

        if len(recent) < MIN_TRADES_PER_WINDOW:
            return {"detected": False,
                    "reason": f"Insufficient data: only {len(recent)} trades in "
                              f"recent window (need {MIN_TRADES_PER_WINDOW})"}
        if len(previous) < MIN_TRADES_PER_WINDOW:
            return {"detected": False,
                    "reason": f"Insufficient data: only {len(previous)} trades in "
                              f"previous window (need {MIN_TRADES_PER_WINDOW})"}

        recent_wr = sum(1 for t in recent if t.get("outcome") == "WIN") / len(recent)
        prev_wr = sum(1 for t in previous if t.get("outcome") == "WIN") / len(previous)
        drift = recent_wr < prev_wr - 0.15

        return {
            "detected": drift,
            "recent_wr": round(recent_wr * 100, 1),
            "previous_wr": round(prev_wr * 100, 1),
            "drop": round((prev_wr - recent_wr) * 100, 1),
            "message": (
                f"WR dropped {(prev_wr - recent_wr) * 100:.0f}pp "
                f"({prev_wr * 100:.0f}% -> {recent_wr * 100:.0f}%)"
                if drift else "stable"
            ),
        }

    def _check_pattern_drift(self) -> dict:
        """A high-confidence (>=0.75) KB pattern whose recently-matching sims
        are losing 3+ times, >70% of the time, has drifted."""
        kb = self.journal.get_active_knowledge(min_confidence=0.0)
        all_sims = self._load_all_sims()

        drifted_patterns = []
        for entry in kb:
            if entry.confidence < 0.75:
                continue

            matching_recent = [
                t for t in all_sims[-50:]
                if entry.observed_in_regime in (t.get("regime", ""), t.get("trend", ""))
                and abs((t.get("rsi") or 50) - 52) < 10
            ]
            if len(matching_recent) < MIN_RECENT_MATCHES:
                continue  # e.g. 5-of-7 = 71% "loss rate" is noise, not evidence

            recent_losses = sum(1 for t in matching_recent if t.get("outcome") == "LOSS")
            if recent_losses >= 3 and (recent_losses / len(matching_recent) > 0.70):
                drifted_patterns.append({
                    "pattern_id": entry.pattern_id,
                    "old_confidence": entry.confidence,
                    "recent_loss_rate": round(recent_losses / len(matching_recent), 3),
                })

        return {
            "detected": len(drifted_patterns) > 0,
            "drifted_count": len(drifted_patterns),
            "patterns": drifted_patterns[:3],
        }

    def _check_regime_drift(self) -> dict:
        """Checks if the current market regime has shifted from what most of
        the recent simulation history was gathered in."""
        try:
            from src.data.regime_detector import RegimeDetector

            current_regime = RegimeDetector().detect().get("regime", "UNKNOWN")
        except Exception:
            current_regime = "UNKNOWN"

        all_sims = self._load_all_sims()

        # `market_regime` is the market-wide category (RegimeDetector's
        # BULL_TRENDING/BEAR_TRENDING/VOLATILE/RANGE_BOUND/RECOVERY/TRANSITIONAL),
        # captured at record-write time. Records predating this field used
        # "regime" to mean the stock-level trend (UPTREND/DOWNTREND) instead —
        # a disjoint vocabulary — so legacy records are excluded rather than
        # inferred/mapped, same as the insufficient-data guards elsewhere here.
        tagged = [t for t in all_sims[-50:] if "market_regime" in t]
        if len(tagged) < MIN_REGIME_TAGGED_RECORDS:
            return {"detected": False,
                    "reason": f"Insufficient market_regime-tagged data: only "
                              f"{len(tagged)} records (need {MIN_REGIME_TAGGED_RECORDS})"}

        regime_counts: dict = defaultdict(int)
        for t in tagged:
            regime_counts[t["market_regime"]] += 1

        dominant = max(regime_counts, key=regime_counts.get)
        dominant_pct = regime_counts[dominant] / sum(regime_counts.values())
        drift = dominant != current_regime and dominant_pct > 0.60

        return {
            "detected": drift,
            "trained_on": dominant,
            "current": current_regime,
            "message": (
                f"Model trained on {dominant} but market is now {current_regime}"
                if drift else "regime stable"
            ),
        }

    # ── response ─────────────────────────────────────────────────────────────
    def _apply_confidence_decay(self) -> None:
        """Decays all knowledge-base pattern confidences by 20% (floor 0.05) —
        stale rules must re-earn confidence through fresh evidence once drift
        is detected, rather than continuing to dominate decisions unchanged."""
        from src.analytics.strategy_scorecard import log_adaptation

        kb = self.journal.get_active_knowledge(min_confidence=0.0)
        decayed = 0
        for entry in kb:
            if entry.confidence > 0.10:
                new_conf = max(0.05, entry.confidence * 0.80)
                self.journal.update_knowledge_confidence(
                    entry.pattern_id, confirmed=None, force_confidence=new_conf)
                decayed += 1
        if decayed:
            log_adaptation(
                "drift", f"Confidence decay applied to {decayed} patterns (x0.80, floor 0.05)",
                f"{len(kb)} active patterns at decay time")

    # ── data + logging ──────────────────────────────────────────────────────
    def _load_all_sims(self) -> list:
        all_trades: list = []
        for fname in (
            "data/cache/forced_trades_history.json",
            "data/cache/continuous_sim_history.json",
            "data/cache/intraday_sim_history.json",
        ):
            p = Path(fname)
            if p.exists():
                try:
                    all_trades.extend(json.loads(p.read_text()))
                except Exception:
                    pass
        return sorted(all_trades, key=lambda x: x.get("simulated_at", x.get("opened_at", "")))

    def _log_drift(self, results: dict) -> None:
        history = []
        if DRIFT_HISTORY_FILE.exists():
            try:
                history = json.loads(DRIFT_HISTORY_FILE.read_text())
            except Exception:
                pass
        history.append(results)
        try:
            DRIFT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            DRIFT_HISTORY_FILE.write_text(json.dumps(history[-100:]))
        except OSError:
            pass
