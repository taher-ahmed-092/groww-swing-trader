"""
Agent evaluation framework — measures whether each agent is actually CORRECT.

Our agents state things confidently (UPTREND, STRONG ROCE, BUY, 8.5/10). We can
check if they were right by what actually happened to the trade. This feedback loop
is what makes the system smarter. Runs weekly; powers the self-improvement engine.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from config.risk_limits import LIMITS
from src.memory.journal import TradingJournal


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_corr(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation, guarded against zero-variance (returns 0.0)."""
    import numpy as np

    if len(xs) < 2:
        return 0.0
    if np.std(xs) == 0 or np.std(ys) == 0:
        return 0.0
    c = float(np.corrcoef(xs, ys)[0, 1])
    return 0.0 if c != c else c  # NaN guard


class AgentEvaluator:
    def __init__(self, journal: TradingJournal | None = None) -> None:
        self.journal = journal or TradingJournal()

    def _closed(self):
        return [t for t in self.journal.get_recent(n=50) if t.outcome in ("WIN", "LOSS")]

    @staticmethod
    def _snap(trade) -> dict:
        try:
            return json.loads(trade.state_snapshot or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}

    def evaluate_all(self) -> dict:
        return {
            "fundamental": self.evaluate_fundamental_accuracy(),
            "technical": self.evaluate_technical_accuracy(),
            "judge": self.evaluate_judge_calibration(),
            "scout": self.evaluate_scout_accuracy(),
            "gut_check": self.evaluate_gut_accuracy(),
            "evaluated_at": _now_iso(),
        }

    def evaluate_fundamental_accuracy(self) -> dict:
        closed = self._closed()
        if len(closed) < 5:
            return {"status": "insufficient_data", "trades_analyzed": len(closed),
                    "message": f"Need 5+ closed trades. Have {len(closed)}."}

        fund_scores, pnl_pcts, paired = [], [], []
        for t in closed:
            score = self._snap(t).get("fundamental_verdict", {}).get("score", 0) or 0
            if score > 0:
                fund_scores.append(score)
                pnl_pcts.append(t.pnl_pct or 0)
                paired.append((score, t))
        if len(fund_scores) < 5:
            return {"status": "insufficient_data", "trades_analyzed": len(fund_scores)}

        correlation = _safe_corr(fund_scores, pnl_pcts)
        high = [t for s, t in paired if s >= 0.75]
        low = [t for s, t in paired if s < 0.55]
        high_wr = sum(1 for t in high if t.outcome == "WIN") / len(high) if high else 0
        low_wr = sum(1 for t in low if t.outcome == "WIN") / len(low) if low else 0

        if correlation > 0.4:
            label = "EXCELLENT — fundamental scores predict outcomes well"
            suggestion = "Fundamental analysis is well-calibrated. No changes needed."
        elif correlation > 0.2:
            label = "GOOD — moderate correlation with outcomes"
            suggestion = "Consider increasing fundamental score weight in judge."
        elif correlation > 0:
            label = "WEAK — slight correlation, room to improve"
            suggestion = "Review which fundamental factors predict outcomes for your watchlist."
        else:
            label = "POOR — fundamental scores not predicting outcomes"
            suggestion = "Revise fundamental scoring weights — current metrics may not fit your sectors."

        return {
            "score_correlation": round(correlation, 3),
            "correlation_label": label,
            "trades_analyzed": len(fund_scores),
            "high_score_win_rate": round(high_wr, 3),
            "low_score_win_rate": round(low_wr, 3),
            "differential": round(high_wr - low_wr, 3),
            "suggestion": suggestion,
        }

    def evaluate_technical_accuracy(self) -> dict:
        closed = self._closed()
        if len(closed) < 5:
            return {"status": "insufficient_data", "trades_analyzed": len(closed)}

        pattern_wins, pattern_totals = {}, {}
        rsi_zone_wins = rsi_zone_total = buy_wins = buy_total = stop_too_tight = 0
        for t in closed:
            tech = self._snap(t).get("technical_verdict", {})
            indics = tech.get("indicators", {})
            if tech.get("signal") == "BUY":
                buy_total += 1
                if t.outcome == "WIN":
                    buy_wins += 1
            rsi = indics.get("rsi_14") or 0
            if 50 <= rsi <= 65:
                rsi_zone_total += 1
                if t.outcome == "WIN":
                    rsi_zone_wins += 1
            for pattern in tech.get("patterns", []) or []:
                if pattern and pattern != "NONE":
                    pattern_totals[pattern] = pattern_totals.get(pattern, 0) + 1
                    if t.outcome == "WIN":
                        pattern_wins[pattern] = pattern_wins.get(pattern, 0) + 1
            if t.outcome == "LOSS" and t.pnl_pct and abs(t.pnl_pct) < LIMITS.stop_loss_pct * 1.1:
                stop_too_tight += 1

        buy_wr = buy_wins / buy_total if buy_total else 0
        rsi_wr = rsi_zone_wins / rsi_zone_total if rsi_zone_total else 0
        stop_tight_pct = stop_too_tight / len(closed) if closed else 0
        per_pattern = {p: round(pattern_wins.get(p, 0) / pattern_totals[p], 2) for p in pattern_totals}
        best = max(per_pattern.items(), key=lambda x: x[1])[0] if per_pattern else "N/A"
        worst = min(per_pattern.items(), key=lambda x: x[1])[0] if per_pattern else "N/A"

        suggestion = ""
        if buy_wr < 0.5:
            suggestion += "BUY signal win rate below 50% — tighten entry criteria. "
        if stop_tight_pct > 0.4:
            suggestion += "Stops triggering frequently — consider widening ATR multiplier to 2.5×. "
        if not suggestion:
            suggestion = "Technical signals performing well."

        return {
            "buy_signal_win_rate": round(buy_wr, 3),
            "rsi_zone_win_rate": round(rsi_wr, 3),
            "stop_too_tight_pct": round(stop_tight_pct, 3),
            "per_pattern_win_rates": per_pattern,
            "best_signal": best,
            "worst_signal": worst,
            "suggestion": suggestion,
        }

    def evaluate_judge_calibration(self) -> dict:
        closed = self._closed()
        if len(closed) < 5:
            return {"status": "insufficient_data", "trades_analyzed": len(closed)}

        buckets = {
            "9-10": {"wins": 0, "total": 0, "expected_wr": 0.95},
            "8-9": {"wins": 0, "total": 0, "expected_wr": 0.85},
            "7-8": {"wins": 0, "total": 0, "expected_wr": 0.75},
            "6-7": {"wins": 0, "total": 0, "expected_wr": 0.65},
        }
        for t in closed:
            judge_score = self._snap(t).get("judge_verdict", {}).get("overall_score", 0) or 0
            for name, bucket in buckets.items():
                lo, hi = map(float, name.split("-"))
                if lo <= judge_score < hi:
                    bucket["total"] += 1
                    if t.outcome == "WIN":
                        bucket["wins"] += 1
                    break

        calibration_error = n_buckets = 0
        for bucket in buckets.values():
            if bucket["total"] > 0:
                actual = bucket["wins"] / bucket["total"]
                err = abs(actual - bucket["expected_wr"])
                calibration_error += err
                n_buckets += 1
                bucket["actual_win_rate"] = round(actual, 3)
                bucket["calibration_error"] = round(err, 3)

        avg_error = calibration_error / n_buckets if n_buckets else None
        if avg_error is None:
            suggestion = "Not enough data per confidence bucket yet."
        elif avg_error < 0.10:
            suggestion = "Excellent calibration. Judge confidence scores are accurate."
        elif avg_error < 0.20:
            suggestion = "Moderate calibration. Judge may be overconfident — consider raising threshold to 6.5."
        else:
            suggestion = "Poor calibration. Review judge system prompt — it may be too generous."

        return {
            "avg_calibration_error": round(avg_error, 3) if avg_error is not None else None,
            "buckets": buckets,
            "suggestion": suggestion,
            "trades_analyzed": len(closed),
        }

    def evaluate_scout_accuracy(self) -> dict:
        closed = self._closed()
        if len(closed) < 5:
            return {"status": "insufficient_data", "trades_analyzed": len(closed)}
        scores, pnls = [], []
        for t in closed:
            snap = self._snap(t)
            # scout score isn't always snapshotted; fall back to technical score proxy.
            score = snap.get("scout_score") or snap.get("technical_verdict", {}).get("score", 0) or 0
            if score > 0:
                scores.append(score)
                pnls.append(t.pnl_pct or 0)
        if len(scores) < 5:
            return {"status": "insufficient_data", "trades_analyzed": len(scores)}
        corr = _safe_corr(scores, pnls)
        return {
            "score_correlation": round(corr, 3),
            "trades_analyzed": len(scores),
            "suggestion": ("Scout scores track outcomes." if corr > 0.2
                           else "Scout ranking weakly predictive — review scoring weights."),
        }

    def evaluate_gut_accuracy(self) -> dict:
        closed = self._closed()
        overrides = correct = 0
        for t in closed:
            gut = self._snap(t).get("gut_check", {})
            if gut.get("modifies_decision"):
                overrides += 1
                # An override "was right" if it steered away from a loss.
                if t.outcome == "LOSS":
                    correct += 1
        if overrides == 0:
            return {"status": "insufficient_data", "overrides": 0,
                    "message": "No gut overrides recorded yet."}
        return {
            "overrides": overrides,
            "override_accuracy": round(correct / overrides, 3),
            "suggestion": ("Gut overrides are protective." if correct / overrides >= 0.5
                           else "Gut overrides not yet adding value — keep monitoring."),
        }

    def generate_improvement_suggestions(self, eval_results: dict) -> list[str]:
        suggestions: list[str] = []
        fund = eval_results.get("fundamental", {})
        if fund.get("score_correlation", 1) < 0.2 and fund.get("trades_analyzed", 0) >= 5:
            suggestions.append(
                "FUNDAMENTAL: Low score correlation ({:.2f}). Fundamental scoring isn't "
                "predicting outcomes for your watchlist. Consider sector-specific weights or "
                "leaning more on technical signals.".format(fund["score_correlation"])
            )
        tech = eval_results.get("technical", {})
        if tech.get("buy_signal_win_rate", 1) < 0.5:
            suggestions.append(
                "TECHNICAL: BUY win rate is {:.0%}. Below 50% with 1:2 R:R loses money. "
                "Raise RSI lower bound 50→55, require ADX>20, require OBV RISING.".format(
                    tech["buy_signal_win_rate"])
            )
        judge = eval_results.get("judge", {})
        if judge.get("avg_calibration_error") and judge["avg_calibration_error"] > 0.20:
            suggestions.append(
                "JUDGE: Calibration error {:.1%} — overconfident. Raise approval threshold "
                "6.0→7.0 and add a calibration instruction to the prompt.".format(
                    judge["avg_calibration_error"])
            )
        return suggestions
