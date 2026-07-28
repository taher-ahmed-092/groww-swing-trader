"""
Strategy attribution — classifies every trade (real + forced + sim) into one
of four styles at entry time, so per-strategy performance is actually
measurable (previously all forced/cont-sim trades landed as "momentum" by
default, or were simply never tagged — Strategy Performance always showed 0
trades for pairs/mean_reversion/breakout).
"""
from __future__ import annotations

STRATEGIES = ("mean_reversion", "breakout", "pairs_trading", "momentum")


def classify_strategy(indicators: dict, is_pairs: bool = False) -> str:
    """Classifies a trade's strategy from its entry-time indicators.
    Priority: pairs > mean_reversion (oversold + above MA200) >
    breakout (52W high/breakout + volume) > momentum (default)."""
    if is_pairs:
        return "pairs_trading"

    indicators = indicators or {}
    rsi = indicators.get("rsi_14")
    ma200 = indicators.get("ma_200")
    close = indicators.get("close")
    volume_ratio = indicators.get("volume_ratio")
    flags = indicators.get("flags") or []

    if rsi is not None and rsi < 35 and ma200 is not None and close is not None and close > ma200:
        return "mean_reversion"

    is_breakout = ("BREAKOUT" in flags) or (
        indicators.get("near_52w_high") and volume_ratio is not None and volume_ratio > 1.5
    )
    if is_breakout:
        return "breakout"

    return "momentum"


def compute_strategy_stats(trades: list[dict]) -> dict:
    """Per-strategy {trades, win_rate, avg_pnl_pct, profit_factor,
    best_regime} across a batch of closed (WIN/LOSS-outcome) trades. Shared
    by /report and the dashboard's Strategy Performance table."""
    stats: dict[str, dict] = {s: {"trades": [], } for s in STRATEGIES}
    for t in trades:
        if t.get("outcome") not in ("WIN", "LOSS"):
            continue
        strategy = t.get("strategy") or "momentum"
        stats.setdefault(strategy, {"trades": []})
        stats[strategy]["trades"].append(t)

    result: dict[str, dict] = {}
    for strategy, bucket in stats.items():
        trades_list = bucket["trades"]
        n = len(trades_list)
        if n == 0:
            result[strategy] = {
                "trades": 0, "win_rate": None, "avg_pnl_pct": None,
                "profit_factor": None, "best_regime": None,
            }
            continue
        pnls = [t.get("pnl_pct", 0) or 0 for t in trades_list]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        win_rate = len(wins) / n
        gross_wins = sum(wins)
        gross_losses = abs(sum(losses))
        pf = round(gross_wins / gross_losses, 2) if gross_losses > 0 else 999.99

        by_regime: dict[str, list[float]] = {}
        for t in trades_list:
            regime = t.get("market_regime") or t.get("regime") or "UNKNOWN"
            by_regime.setdefault(regime, []).append(t.get("pnl_pct") or 0)
        best_regime = None
        if by_regime:
            regime_wr = {}
            for regime, pnl_list in by_regime.items():
                regime_wr[regime] = sum(1 for p in pnl_list if p > 0) / len(pnl_list)
            best_regime = max(regime_wr, key=regime_wr.get)

        result[strategy] = {
            "trades": n, "win_rate": round(win_rate, 3),
            "avg_pnl_pct": round(sum(pnls) / n, 3), "profit_factor": pf,
            "best_regime": best_regime,
        }
    return result
