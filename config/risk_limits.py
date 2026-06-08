"""
Single source of truth for hard risk limits.
Frozen dataclass — nothing overrides these at runtime.
Change only after deliberate review and git commit.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    # ── Capital & position sizing ───────────────────────────────
    default_trade_value_inr: float = 500.0
    max_risk_per_trade_pct: float = 3.0      # % of total portfolio risked per trade
    max_position_pct: float = 20.0           # % of portfolio in one name (cap)
    max_open_positions: int = 5
    max_trades_per_week: int = 3             # increase only after paper-proven edge

    # ── Stops ───────────────────────────────────────────────────
    # Every trade MUST have a stop. Broker enforces via GTT OCO order.
    stop_loss_pct: float = 7.0               # hard stop below entry
    target_reward_ratio: float = 2.0         # target = entry + (stop_dist * ratio)
                                              # => min risk:reward of 1:2

    # ── Conviction gate ─────────────────────────────────────────
    min_confidence: float = 0.80             # [0.0, 1.0). Never accept 1.0 — it means hallucination.
    require_judge_pass: bool = True
    require_human_approval: bool = True      # LangGraph interrupt before live execution

    # ── Kill switch ─────────────────────────────────────────────
    kill_switch_file: str = "KILL_SWITCH"    # presence of this file halts all execution


LIMITS = RiskLimits()
