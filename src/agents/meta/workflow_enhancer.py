"""
Workflow enhancer — the system's self-improvement engine.

Weekly, it reads agent-evaluation results + journal + knowledge, asks an LLM (Haiku,
tight prompt) for 3 specific improvements, and AUTO-APPLIES the safe, bounded
parameter changes to adaptive_params.json. Non-auto suggestions go to Telegram for
human review. Falls back to heuristics without an API key.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from rich.console import Console

from config.settings import settings
from src.memory.journal import KnowledgeEntry, TradingJournal
from src.utils.adaptive import ADJUSTABLE_PARAMS, get_adaptive_params, save_adaptive_params

console = Console()


def _pid(text: str) -> str:
    return f"workflow-{int(hashlib.md5(text.encode()).hexdigest(), 16) % 10000:04d}"


class WorkflowEnhancer:
    ADJUSTABLE_PARAMS = ADJUSTABLE_PARAMS

    # Cold-start protection: don't tune parameters on thin/seeded evidence.
    MIN_TRADES_FOR_AUTO_APPLY = 15
    MIN_TRADES_FOR_SUGGESTIONS = 5
    MOMENTUM_FILE = Path("data/cache/param_suggestions_history.json")

    def __init__(self, journal: TradingJournal | None = None) -> None:
        self.journal = journal or TradingJournal()

    def _real_closed_count(self) -> int:
        return len([
            t for t in self.journal.get_recent(n=200)
            if t.outcome in ("WIN", "LOSS") and not getattr(t, "is_seeded", False)
        ])

    def _load_momentum(self) -> dict:
        try:
            if self.MOMENTUM_FILE.exists():
                return json.loads(self.MOMENTUM_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _record_suggestion(self, param_name: str, suggested_value) -> None:
        hist = self._load_momentum()
        hist.setdefault(param_name, []).append(suggested_value)
        hist[param_name] = hist[param_name][-3:]  # keep last 3
        try:
            os.makedirs(self.MOMENTUM_FILE.parent, exist_ok=True)
            self.MOMENTUM_FILE.write_text(json.dumps(hist, indent=2))
        except OSError:
            pass

    def _should_apply_change(self, param_name: str, new_value, current_value) -> bool:
        """Momentum guard: require the previous suggestion to point the same way."""
        hist = self._load_momentum().get(param_name, [])
        if not hist:
            return False  # need at least one prior same-direction suggestion
        prev = hist[-1]
        new_dir = (new_value > current_value) - (new_value < current_value)
        prev_dir = (prev > current_value) - (prev < current_value)
        return new_dir != 0 and new_dir == prev_dir

    def load_params(self) -> dict:
        return get_adaptive_params()

    def save_params(self, params: dict) -> None:
        save_adaptive_params(params)

    def run(self, eval_results: dict) -> dict:
        # ── Cold-start protection ──
        n_real = self._real_closed_count()
        if n_real < self.MIN_TRADES_FOR_SUGGESTIONS:
            return {
                "suggestions": [], "auto_applied": [], "params_updated": False,
                "status": "waiting",
                "message": (
                    f"Building evidence… {n_real}/{self.MIN_TRADES_FOR_SUGGESTIONS} "
                    "real (non-seeded) trades needed before tuning."
                ),
            }
        allow_auto_apply = n_real >= self.MIN_TRADES_FOR_AUTO_APPLY

        if settings.has_anthropic_key and not settings.effective_demo_mode:
            suggestions = self._llm_suggestions(eval_results)
        else:
            suggestions = self._heuristic_suggestions(eval_results)

        # Auto-apply safe, bounded changes — only with enough real evidence AND a
        # consistent-direction momentum guard.
        params = self.load_params()
        applied: list[dict] = []
        for s in suggestions:
            if not (s.get("auto_applicable") and s.get("param_name") and s.get("param_new_value") is not None):
                continue
            param = s["param_name"]
            if param not in self.ADJUSTABLE_PARAMS:
                continue
            bounds = self.ADJUSTABLE_PARAMS[param]
            clamped = max(bounds["min"], min(bounds["max"], s["param_new_value"]))
            old_val = params.get(param, bounds["current"])
            # Always record the suggestion direction for the momentum guard.
            same_direction = self._should_apply_change(param, clamped, old_val)
            self._record_suggestion(param, clamped)
            if not allow_auto_apply or not same_direction:
                continue
            if clamped != old_val:
                params[param] = clamped
                applied.append({"param": param, "old_value": old_val,
                                "new_value": clamped, "reason": s.get("problem", "")})

        if applied:
            self.save_params(params)
            console.print(f"[green]Auto-applied {len(applied)} parameter changes[/green]")

        # Record suggestions as SYSTEM_IMPROVEMENT knowledge entries.
        for s in suggestions:
            try:
                self.journal.log_knowledge_entry(
                    KnowledgeEntry(
                        pattern_id=_pid(s.get("problem", "")),
                        pattern_description=f"WORKFLOW: {s.get('change', '')[:100]}",
                        category="SYSTEM_IMPROVEMENT",
                        confidence=0.3 if s.get("priority") == "HIGH" else 0.1,
                        observed_count=1,
                        is_hypothesis=True,
                        last_seen_in_trade="workflow-enhancer",
                    )
                )
            except Exception:
                pass

        return {
            "suggestions": suggestions,
            "auto_applied": applied,
            "params_updated": len(applied) > 0,
        }

    def _build_minimal_context(self, eval_results, closed, knowledge, current_params) -> str:
        tech = eval_results.get("technical", {})
        fund = eval_results.get("fundamental", {})
        judge = eval_results.get("judge", {})
        wins = sum(1 for t in closed if t.outcome == "WIN")
        losses = sum(1 for t in closed if t.outcome == "LOSS")
        total = wins + losses
        wr = f"{wins / total:.0%}" if total else "N/A"
        fails = ", ".join(t.failure_category for t in closed
                          if t.outcome == "LOSS" and getattr(t, "failure_category", None))[:80]
        return (
            f"System performance summary:\nWin/Loss: {wins}/{losses} | WR: {wr}\n"
            f"Fund correlation: {fund.get('score_correlation', 'N/A')}\n"
            f"Tech BUY accuracy: {tech.get('buy_signal_win_rate', 'N/A')}\n"
            f"Judge calibration error: {judge.get('avg_calibration_error', 'N/A')}\n"
            f"Top failure patterns: {fails}\n"
            f"Knowledge: {(knowledge[:200] if knowledge else 'empty')}"
        )

    def _llm_suggestions(self, eval_results: dict) -> list[dict]:
        closed = self.journal.get_recent(n=20)
        knowledge = self.journal.format_knowledge_for_context(min_confidence=0.3)
        current_params = self.load_params()
        context = self._build_minimal_context(eval_results, closed, knowledge, current_params)

        system = (
            "You are a quantitative trading system optimizer. You receive performance data "
            "and suggest 3 specific, actionable improvements. Be precise. No vague advice. "
            "Focus on what's causing losses and what parameters would improve signal quality. "
            "Budget: respond in under 400 tokens total."
        )
        user = context + "\n\nCurrent adjustable parameters: " + json.dumps(current_params) + """

Suggest exactly 3 improvements. Return a JSON array of 3 objects:
[{"problem": str, "change": str, "auto_applicable": bool,
  "param_name": str or null, "param_new_value": number or null,
  "priority": "HIGH"|"MEDIUM"|"LOW", "expected_impact": str}]"""

        try:
            from anthropic import Anthropic

            client = Anthropic(api_key=settings.anthropic_api_key)
            response = client.messages.create(
                model=settings.llm_model_default,
                max_tokens=400,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            text = response.content[0].text.replace("```json", "").replace("```", "").strip()
            suggestions = json.loads(text)
            if isinstance(suggestions, list) and suggestions:
                return suggestions
        except Exception as exc:
            console.print(f"[yellow]Workflow enhancer LLM failed: {exc}[/yellow]")
        return self._heuristic_suggestions(eval_results)

    def _heuristic_suggestions(self, eval_results: dict) -> list[dict]:
        suggestions: list[dict] = []
        tech = eval_results.get("technical", {})
        if tech.get("buy_signal_win_rate", 0.6) < 0.5:
            suggestions.append({
                "problem": "BUY signal win rate below 50%",
                "change": "Raise RSI lower bound from 50 to 55",
                "auto_applicable": True,
                "param_name": "scout_rsi_low",
                "param_new_value": 55,
                "priority": "HIGH",
                "expected_impact": "Filters out weaker momentum signals",
            })
        judge = eval_results.get("judge", {})
        if judge.get("avg_calibration_error") and judge["avg_calibration_error"] > 0.20:
            suggestions.append({
                "problem": "Judge is overconfident (calibration error > 20%)",
                "change": "Raise judge approval threshold from 6.0 to 6.5",
                "auto_applicable": True,
                "param_name": "judge_approval_threshold",
                "param_new_value": 6.5,
                "priority": "HIGH",
                "expected_impact": "Fewer false-positive approvals",
            })
        while len(suggestions) < 3:
            suggestions.append({
                "problem": "Insufficient data for optimization",
                "change": "Continue paper trading to accumulate more data",
                "auto_applicable": False,
                "param_name": None,
                "param_new_value": None,
                "priority": "LOW",
                "expected_impact": "More data → better optimization",
            })
        return suggestions
