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
    max_positions_per_sector: int = 2        # diversification: never >2 open in one sector
    max_trades_per_week: int = 3             # increase only after paper-proven edge

    # ── Stops ───────────────────────────────────────────────────
    # Every trade MUST have a stop. Broker enforces via GTT OCO order.
    stop_loss_pct: float = 7.0               # hard stop below entry
    target_reward_ratio: float = 2.0         # target = entry + (stop_dist * ratio)
                                              # => min risk:reward of 1:2
    # Nearest pivot R1 only caps the target when it sits at least this many
    # multiples of the stop distance above entry — R1 = 2*pivot - prior_low
    # routinely lands 0.5-1% above entry for a stock near its recent high,
    # which is noise relative to a 7% stop (gross R:R ~0.1:1) and was firing
    # POOR_RISK_REWARD on nearly every candidate. Below this ratio, R1 is
    # not a meaningful target and the cost-aware target is used instead.
    min_resistance_distance_ratio: float = 1.5

    # ── Conviction gate ─────────────────────────────────────────
    min_confidence: float = 0.80             # [0.0, 1.0). Never accept 1.0 — it means hallucination.
    require_judge_pass: bool = True
    require_human_approval: bool = True      # LangGraph interrupt before live execution

    # ── Kill switch ─────────────────────────────────────────────
    kill_switch_file: str = "KILL_SWITCH"    # presence of this file halts all execution

    # ── Daily loss circuit breaker ──────────────────────────────
    # Real-money-protection threshold — computed ONLY from real TradingJournal
    # trades (src/risk/checker.py). Forced/intraday/short simulation P&L never
    # counts toward this: those are learning-volume simulations, not capital
    # at risk, and must never halt real signal evaluation.
    daily_loss_limit_pct: float = -6.0


LIMITS = RiskLimits()
