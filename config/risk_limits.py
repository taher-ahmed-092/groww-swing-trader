"""Single source of truth for hard risk limits. Nothing overrides these."""
from dataclasses import dataclass

@dataclass(frozen=True)
class RiskLimits:
    # --- Capital & sizing ---
    default_trade_value_inr: float = 500.0
    max_risk_per_trade_pct: float = 3.0      # max % of portfolio risked per trade
    max_position_pct: float = 20.0           # max % of portfolio in one name
    max_open_positions: int = 5
    max_trades_per_week: int = 3             # bump only after paper-proven edge

    # --- Stops (every trade MUST have one) ---
    stop_loss_pct: float = 7.0               # hard stop
    trailing_stop_enabled: bool = True

    # --- Conviction gate ---
    min_confidence: float = 0.80             # do not act below this; never 1.0 fantasy
    require_judge_pass: bool = True          # LLM-as-judge must approve

    # --- Safety switches ---
    live_trading_enabled: bool = False       # PAPER by default. Flip via env, deliberately.
    kill_switch_file: str = "KILL_SWITCH"    # if this file exists, halt all execution

LIMITS = RiskLimits()