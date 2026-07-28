"""
Three trading modes — a single risk dial for the whole system.

  🛡️ conserve  — capital protection. High judge bar, fewer trades, half size.
  ⚖️ balanced  — the default. Moderate bar, full Kelly size.
  ⚡ rogue      — eager learning. Low bar, more trades, will trade downtrends.

The mode is read at decision time from a runtime override file (set via the
/conserve /balanced /rogue Telegram commands) and falls back to settings.trading_mode
(from .env). It governs the judge approval threshold, weekly trade cap, position
sizing, and whether the judge auto-vetoes longs in a downtrend.

NON-NEGOTIABLE: no mode ever removes the stop-loss or skips the judge. Rogue lowers
the bar; it does not bypass the pipeline (CLAUDE.md rules 2 & 3).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_MODE_FILE = Path("data/cache/trading_mode.txt")


@dataclass(frozen=True)
class TradingModeConfig:
    name: str
    emoji: str
    judge_threshold: float          # approval bar out of 10
    max_trades_per_week: int
    position_size_multiplier: float  # applied on top of Kelly + tier sizing
    trades_downtrends: bool          # if True, judge won't auto-veto longs in a downtrend
    require_adx_trending: bool       # if True, judge auto-vetoes CHOPPY_MARKET (choppy ADX)
    description: str


MODES: dict[str, TradingModeConfig] = {
    "conserve": TradingModeConfig(
        name="conserve", emoji="🛡️", judge_threshold=8.0, max_trades_per_week=2,
        position_size_multiplier=0.5, trades_downtrends=False, require_adx_trending=True,
        description="Capital protection. High bar, fewer trades, half position size."),
    "balanced": TradingModeConfig(
        name="balanced", emoji="⚖️", judge_threshold=6.5, max_trades_per_week=3,
        position_size_multiplier=1.0, trades_downtrends=False, require_adx_trending=False,
        description="Default. Moderate bar, full Kelly size."),
    "rogue": TradingModeConfig(
        name="rogue", emoji="⚡", judge_threshold=5.5, max_trades_per_week=5,
        position_size_multiplier=1.0, trades_downtrends=True, require_adx_trending=False,
        description="Eager learning. Low bar, more trades, will trade downtrends."),
}

DEFAULT_MODE = "balanced"


def get_mode_config(mode: str | None) -> TradingModeConfig:
    return MODES.get((mode or DEFAULT_MODE).strip().lower(), MODES[DEFAULT_MODE])


def get_current_mode() -> TradingModeConfig:
    """Active mode: runtime override file (Telegram) first, then .env, then default."""
    try:
        if _MODE_FILE.exists():
            m = _MODE_FILE.read_text().strip().lower()
            if m in MODES:
                return MODES[m]
    except OSError:
        pass
    try:
        from config.settings import settings

        return get_mode_config(getattr(settings, "effective_trading_mode", DEFAULT_MODE))
    except Exception:
        return MODES[DEFAULT_MODE]


def set_mode(mode: str) -> TradingModeConfig:
    """Persist a runtime mode override (used by the /conserve /balanced /rogue commands)."""
    cfg = get_mode_config(mode)
    try:
        _MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MODE_FILE.write_text(cfg.name)
    except OSError:
        pass
    return cfg
