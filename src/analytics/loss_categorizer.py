"""Attributes a closed LOSS to a category using its entry_snapshot (indicators,
market_context, scores, entry_reason captured at entry time — see
TradingJournal.log_proposed, ContinuousSimulator, AlwaysOnTrader). Genuine
post-mortems need to know WHY a trade lost, not just that it did; without the
entry-time snapshot every RCA note is either a guess or requires re-fetching
historical data that may no longer even be available.

Categories, in priority order (a loss usually has more than one plausible
cause — first match wins, ordered from most to least specific):
  PRE_SNAPSHOT           trade predates the entry_snapshot field — genuinely
                         uncategorizable, not "no cause found" (see below)
  STOP_TOO_TIGHT        stop distance was < 1x ATR at entry
  OVEREXTENDED          RSI > 68 at entry — bought into an already-extended move
  WEAK_TREND_CONFIRMATION  ADX signal was CHOPPY/NEUTRAL at entry
  REGIME_MISMATCH       market regime at entry was bearish/volatile for a long entry
  MARKET_WIDE_DROP       Nifty fell more than the stock did over the hold
  NEWS_SHOCK             exit gapped > 2x entry-time ATR against the position
  COSTS_ATE_PROFIT       gross was a small win but net (after costs) is a loss
  UNKNOWN                a snapshot exists but none of the rules above matched

PRE_SNAPSHOT is a distinct bucket from UNKNOWN on purpose: a trade with no
entry_snapshot at all (it predates the field) has NO data to categorize from,
whereas UNKNOWN means data existed but didn't match a known pattern. Lumping
them together made "UNKNOWN: 328" look like an analysis gap when it was
mostly just old trades — see PRE_SNAPSHOT_LABEL / breakdown()'s exclusion.
"""
from __future__ import annotations

PRE_SNAPSHOT_LABEL = "PRE-SNAPSHOT (no data)"
UNCATEGORISED_LABEL = "UNCATEGORISED (snapshot present)"

CATEGORIES = (
    PRE_SNAPSHOT_LABEL, "STOP_TOO_TIGHT", "OVEREXTENDED", "WEAK_TREND_CONFIRMATION",
    "REGIME_MISMATCH", "MARKET_WIDE_DROP", "NEWS_SHOCK", "COSTS_ATE_PROFIT", UNCATEGORISED_LABEL,
)

_BEARISH_REGIMES = {"BEAR_TRENDING", "VOLATILE", "TRANSITIONAL"}
_CHOPPY_ADX = {"CHOPPY", "NEUTRAL"}


def categorize_loss(trade: dict, entry_snapshot: dict | None) -> str:
    """`trade` carries at least entry/stop/exit prices and, where available,
    gross_pnl_pct/pnl_pct and nifty-at-exit context. `entry_snapshot` is the
    nested dict captured at entry (indicators/market_context/scores/entry_reason).
    Returns one of CATEGORIES; never raises — a missing field just skips that
    check and falls through toward UNKNOWN."""
    if not entry_snapshot or not entry_snapshot.get("indicators"):
        return PRE_SNAPSHOT_LABEL

    snap = entry_snapshot
    indicators = snap.get("indicators") or {}
    market_context = snap.get("market_context") or {}

    entry = trade.get("entry") or trade.get("entry_price")
    stop = trade.get("stop") or trade.get("stop_price")
    exit_price = trade.get("exit") or trade.get("exit_price")
    atr = indicators.get("atr_14")

    if entry and stop and atr and atr > 0:
        stop_distance = abs(entry - stop)
        if stop_distance < atr:
            return "STOP_TOO_TIGHT"

    rsi = indicators.get("rsi_14")
    if rsi is not None and rsi > 68:
        return "OVEREXTENDED"

    adx_signal = indicators.get("adx_signal")
    if adx_signal in _CHOPPY_ADX:
        return "WEAK_TREND_CONFIRMATION"

    regime = market_context.get("regime")
    if regime in _BEARISH_REGIMES:
        return "REGIME_MISMATCH"

    nifty_entry = market_context.get("nifty_price")
    nifty_exit = trade.get("nifty_exit_price")
    if nifty_entry and nifty_exit and entry and exit_price:
        nifty_move_pct = (nifty_exit - nifty_entry) / nifty_entry * 100
        stock_move_pct = (exit_price - entry) / entry * 100
        if nifty_move_pct < 0 and nifty_move_pct < stock_move_pct - 1.0:
            return "MARKET_WIDE_DROP"

    if entry and exit_price and atr and atr > 0:
        gap = abs(exit_price - entry)
        if gap > 2 * atr:
            return "NEWS_SHOCK"

    gross_pct = trade.get("gross_pnl_pct")
    net_pct = trade.get("pnl_pct")
    if gross_pct is not None and net_pct is not None and gross_pct > 0 and net_pct <= 0:
        return "COSTS_ATE_PROFIT"

    return UNCATEGORISED_LABEL


def breakdown(trades_with_snapshots: list[tuple[dict, dict | None]]) -> dict[str, int]:
    """Counts categories across a batch of (trade, entry_snapshot) pairs —
    only LOSS-outcome trades should be passed in. Always includes every
    category key (0 if absent) so callers can render a stable table."""
    counts = {cat: 0 for cat in CATEGORIES}
    for trade, snapshot in trades_with_snapshots:
        counts[categorize_loss(trade, snapshot)] += 1
    return counts


def breakdown_for_chart(trades_with_snapshots: list[tuple[dict, dict | None]]) -> tuple[dict[str, int], int]:
    """Same as breakdown(), but excludes PRE_SNAPSHOT entirely from the
    returned dict (chart dataset) — it is reported back separately as a
    footnote count so the chart isn't dominated by trades that predate the
    entry_snapshot field entirely (no data to categorize, not "uncategorized")."""
    counts = breakdown(trades_with_snapshots)
    pre_snapshot_count = counts.pop(PRE_SNAPSHOT_LABEL, 0)
    return counts, pre_snapshot_count
