"""
Pre-trade risk checker — the hard gate before execution.

Every check below can reject a trade. All limits come from config.risk_limits
(CLAUDE.md rule 7 — never hardcode them here). Returns a structured verdict; never
raises on a normal rejection.
"""
from __future__ import annotations

import math
import os

from rich.console import Console

from config.risk_limits import LIMITS
from src.orchestrator.state import TradeState
from src.risk.position_sizer import PositionSizer

console = Console()

DEFAULT_PORTFOLIO_VALUE_INR = 1500.0


class RiskChecker:
    def __init__(self, portfolio_value_inr: float = DEFAULT_PORTFOLIO_VALUE_INR,
                 open_positions: int = 0) -> None:
        self.portfolio_value_inr = portfolio_value_inr
        self.open_positions = open_positions

    def _reject(self, reasons: list[str]) -> dict:
        for r in reasons:
            console.print(f"[red][RISK] REJECTED: {r}[/red]")
        return {
            "approved": False,
            "reasons": reasons,
            "position_size_inr": 0.0,
            "quantity": 0,
            "risk_inr": 0.0,
        }

    def check(self, state: TradeState) -> dict:
        reasons: list[str] = []

        technical = state.get("technical_verdict", {})
        fundamental = state.get("fundamental_verdict", {})
        judge = state.get("judge_verdict", {})

        # 1. Kill switch.
        if os.path.exists(LIMITS.kill_switch_file):
            return self._reject(["KILL_SWITCH file present — all execution halted."])

        # 2. Confidence floor on both legs.
        tech_score = technical.get("score", 0) or 0
        fund_score = fundamental.get("score", 0) or 0
        if tech_score < LIMITS.min_confidence:
            reasons.append(
                f"technical score {tech_score} < min_confidence {LIMITS.min_confidence}"
            )
        if fund_score < LIMITS.min_confidence:
            reasons.append(
                f"fundamental score {fund_score} < min_confidence {LIMITS.min_confidence}"
            )

        # 3. Stop must exist — no exceptions.
        stop_price = technical.get("stop_price")
        if not stop_price:
            reasons.append("no stop_price set — every trade requires a stop.")

        # 4. Judge pass.
        if LIMITS.require_judge_pass and not judge.get("approved"):
            reasons.append("judge did not approve the trade.")

        # 5. Open position cap.
        if self.open_positions >= LIMITS.max_open_positions:
            reasons.append(
                f"open positions {self.open_positions} >= max {LIMITS.max_open_positions}"
            )

        # 6. Gut-check gate — a holistic veto beyond the checklist.
        gut = state.get("gut_check", {}) or {}
        gut_score = gut.get("gut_score", 5.0)
        if gut_score < 3.0:
            reasons.append(
                "Gut check: strong NO — "
                + (gut.get("override_reason") or gut.get("gut_reasoning", "low conviction"))
            )
        elif gut.get("modifies_decision") and gut_score < 4.0:
            reasons.append("Gut override: " + (gut.get("override_reason") or "gut vetoed"))

        # 7. Valid entry price required before sizing.
        entry_price = technical.get("entry_price")
        if not entry_price or (isinstance(entry_price, float) and math.isnan(entry_price)):
            reasons.append("no valid entry_price — cannot size position.")
            return self._reject(reasons)

        if reasons:
            return self._reject(reasons)

        # 8. Dynamic position sizing (Kelly × confidence × regime).
        sizing = PositionSizer().calculate(state, self.portfolio_value_inr)
        quantity = sizing["quantity"]
        position_size_inr = sizing["position_size_inr"]
        if quantity < 1:
            reasons.append("dynamic sizing produced < 1 share — entry price too high for capital.")
            return self._reject(reasons)

        # 9. Portfolio risk cap (on the actual deployed quantity).
        risk_inr = round(quantity * entry_price * (LIMITS.stop_loss_pct / 100), 4)
        max_risk_inr = self.portfolio_value_inr * (LIMITS.max_risk_per_trade_pct / 100)
        if risk_inr > max_risk_inr:
            reasons.append(
                f"risk {risk_inr} INR > max allowed {round(max_risk_inr, 4)} INR "
                f"({LIMITS.max_risk_per_trade_pct}% of {self.portfolio_value_inr})"
            )
            return self._reject(reasons)

        return {
            "approved": True,
            "reasons": ["all risk checks passed"],
            "position_size_inr": position_size_inr,
            "quantity": quantity,
            "risk_inr": risk_inr,
            "kelly_fraction": sizing["kelly_fraction"],
            "win_rate_used": sizing["win_rate_used"],
            "confidence_multiplier": sizing["confidence_multiplier"],
            "confidence_tier": sizing["confidence_tier"],
            "sizing_explanation": sizing["sizing_explanation"],
            "sizing_details": sizing,
        }
