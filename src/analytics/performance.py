"""
Performance analytics over the trade journal.

Drives the weekly Telegram report and the trading cool-down circuit breaker
(consecutive losses / drawdown).
"""
from __future__ import annotations

from sqlmodel import Session, select

from src.memory.journal import TradeRecord, TradingJournal

DEFAULT_STARTING_CAPITAL = 1500.0


class PerformanceAnalyzer:
    def __init__(self, journal: TradingJournal | None = None,
                 starting_capital: float = DEFAULT_STARTING_CAPITAL) -> None:
        self.journal = journal or TradingJournal()
        self.starting_capital = starting_capital

    def get_strategy_breakdown(self) -> dict:
        """Win rate / count / avg P&L / best regime per strategy (the learning made visible)."""
        import json

        closed = self._closed()
        buckets: dict[str, list] = {}
        for t in closed:
            buckets.setdefault(getattr(t, "strategy_name", "momentum") or "momentum", []).append(t)

        breakdown: dict[str, dict] = {}
        for strat in ("momentum", "mean_reversion", "breakout", "pairs_trading"):
            rows = buckets.get(strat, [])
            n = len(rows)
            wins = sum(1 for t in rows if t.outcome == "WIN")
            avg_pnl = round(sum(t.pnl_pct or 0 for t in rows) / n, 2) if n else 0.0
            # Best regime = highest win rate among regimes with >= 2 trades.
            regime_stats: dict[str, list] = {}
            for t in rows:
                try:
                    rg = json.loads(t.state_snapshot or "{}").get("market_context", {}).get("regime")
                except (json.JSONDecodeError, TypeError):
                    rg = None
                if rg:
                    regime_stats.setdefault(rg, []).append(1 if t.outcome == "WIN" else 0)
            best_regime = "—"
            best_wr = -1.0
            for rg, outcomes in regime_stats.items():
                if len(outcomes) >= 2:
                    wr = sum(outcomes) / len(outcomes)
                    if wr > best_wr:
                        best_wr, best_regime = wr, rg
            breakdown[strat] = {
                "win_rate": round(wins / n, 4) if n else None,
                "trades": n, "avg_pnl_pct": avg_pnl, "best_regime": best_regime,
            }
        return breakdown

    def get_performance_trajectory(self) -> dict:
        """Compare the last 10 closed trades' win rate to the prior 10."""
        closed = self._closed()
        wins = [1 if t.outcome == "WIN" else 0 for t in closed]
        if len(wins) < 6:
            return {
                "trajectory": "INSUFFICIENT_DATA", "trajectory_emoji": "🌱",
                "context_sentence": "Too few closed trades to judge trajectory yet — keep paper trading.",
            }
        recent = wins[-10:]
        prior = wins[-20:-10] or wins[:-10]
        recent_wr = sum(recent) / len(recent)
        prior_wr = (sum(prior) / len(prior)) if prior else recent_wr
        delta = recent_wr - prior_wr
        if delta > 0.1:
            traj, emoji = "IMPROVING", "📈"
        elif delta < -0.1:
            traj, emoji = "DECLINING", "📉"
        else:
            traj, emoji = "STABLE", "➡️"
        sentence = (
            f"{emoji} Trajectory {traj}: last-10 win rate {recent_wr:.0%} "
            f"vs prior-10 {prior_wr:.0%}."
        )
        return {"trajectory": traj, "trajectory_emoji": emoji, "context_sentence": sentence}

    def _closed(self) -> list[TradeRecord]:
        with Session(self.journal.engine) as session:
            stmt = (
                select(TradeRecord)
                .where(TradeRecord.outcome != "OPEN")
                .order_by(TradeRecord.id.asc())
            )
            return list(session.exec(stmt).all())

    def get_summary(self) -> dict:
        trades = self._closed()
        total = len(trades)
        empty = {
            "total_trades": 0, "win_rate": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
            "expectancy": 0.0, "best_trade": {}, "worst_trade": {},
            "total_pnl_inr": 0.0, "consecutive_losses": 0,
        }
        if total == 0:
            return empty

        wins = [t for t in trades if t.outcome == "WIN"]
        losses = [t for t in trades if t.outcome == "LOSS"]
        win_rate = len(wins) / total

        def _avg_pct(rows):
            vals = [r.pnl_pct for r in rows if r.pnl_pct is not None]
            return round(sum(vals) / len(vals), 4) if vals else 0.0

        avg_win_pct = _avg_pct(wins)
        avg_loss_pct = _avg_pct(losses)
        expectancy = round(
            (win_rate * avg_win_pct) - ((1 - win_rate) * abs(avg_loss_pct)), 4
        )

        with_pct = [t for t in trades if t.pnl_pct is not None]
        best = max(with_pct, key=lambda t: t.pnl_pct, default=None)
        worst = min(with_pct, key=lambda t: t.pnl_pct, default=None)
        total_pnl = round(sum(t.pnl for t in trades if t.pnl is not None), 4)

        # Current consecutive-loss streak (walk back from most recent).
        streak = 0
        for t in reversed(trades):
            if t.outcome == "LOSS":
                streak += 1
            else:
                break

        return {
            "total_trades": total,
            "win_rate": round(win_rate, 4),
            "avg_win_pct": avg_win_pct,
            "avg_loss_pct": avg_loss_pct,
            "expectancy": expectancy,
            "best_trade": {"symbol": best.symbol, "pnl_pct": best.pnl_pct} if best else {},
            "worst_trade": {"symbol": worst.symbol, "pnl_pct": worst.pnl_pct} if worst else {},
            "total_pnl_inr": total_pnl,
            "consecutive_losses": streak,
        }

    def get_sector_breakdown(self) -> dict[str, dict]:
        trades = self._closed()
        buckets: dict[str, list[TradeRecord]] = {}
        for t in trades:
            sector = t.sector or "Unknown"
            buckets.setdefault(sector, []).append(t)

        breakdown: dict[str, dict] = {}
        for sector, rows in buckets.items():
            wins = sum(1 for r in rows if r.outcome == "WIN")
            n = len(rows)
            breakdown[sector] = {
                "trades": n,
                "win_rate": round(wins / n, 4) if n else 0.0,
                "total_pnl_inr": round(sum(r.pnl for r in rows if r.pnl is not None), 4),
            }
        return breakdown

    def get_weekly_report(self) -> str:
        s = self.get_summary()
        sectors = self.get_sector_breakdown()
        ranked = sorted(sectors.items(), key=lambda kv: kv[1]["win_rate"], reverse=True)
        top = [f"{name} ({d['win_rate'] * 100:.0f}%)" for name, d in ranked[:3]]
        bottom = [f"{name} ({d['win_rate'] * 100:.0f}%)" for name, d in ranked[-3:]] if ranked else []

        best = s["best_trade"]
        worst = s["worst_trade"]
        best_str = f"{best.get('symbol', '—')} +{best.get('pnl_pct', 0)}%" if best else "—"
        worst_str = f"{worst.get('symbol', '—')} {worst.get('pnl_pct', 0)}%" if worst else "—"

        streak = s["consecutive_losses"]
        streak_line = f"Consecutive losses: {streak}"
        if streak > 3:
            streak_line += "  ⚠️ COOL-DOWN RECOMMENDED"

        return (
            "📈 Weekly Performance Report\n"
            f"Trades: {s['total_trades']} | Win Rate: {s['win_rate'] * 100:.0f}% | "
            f"Total P&L: ₹{s['total_pnl_inr']}\n"
            f"Best: {best_str} | Worst: {worst_str}\n"
            f"Expectancy per trade: ₹{s['expectancy']}\n"
            f"Top sectors: {', '.join(top) if top else 'n/a'}\n"
            f"Bottom sectors: {', '.join(bottom) if bottom else 'n/a'}\n"
            f"{streak_line}"
        )

    def get_professional_metrics(self) -> dict:
        """The metrics professional platforms lead with — profit factor, max
        drawdown, and strategy-decay detection. Computed across ALL trade
        sources (real + simulated), not just the real pipeline."""
        from src.analytics.trade_loader import load_all_trade_history

        trades = load_all_trade_history(self.journal)
        if not trades:
            return {"status": "no_data"}

        pnls = [t.get("pnl", 0) for t in trades]
        gross_wins = sum(p for p in pnls if p > 0)
        gross_losses = abs(sum(p for p in pnls if p < 0))
        # Cap rather than return float('inf') — "Infinity" isn't valid JSON and
        # would silently break JSON.parse() on the dashboard's WebSocket payload.
        profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else 999.99

        # Max drawdown on an R-multiple equity curve, NOT raw pnl_pct summed.
        # Bug found live: summing pnl_pct (a per-trade % return on price) across
        # hundreds of trades as if it were an equity balance produced "581.5%"
        # drawdown — a meaningless number. An R-multiple curve normalizes each
        # trade to "how many multiples of its own risk did it make/lose," which
        # is what a real equity curve assuming constant 1%-of-capital risk per
        # trade would track. Falls back to a 3.0% stop distance when entry/stop
        # aren't available (some sim sources don't carry them).
        def _r_multiple(t: dict) -> float:
            entry, stop, pnl = t.get("entry"), t.get("stop"), t.get("pnl", 0)
            if entry and stop and entry > 0:
                stop_distance_pct = abs(entry - stop) / entry * 100
            else:
                stop_distance_pct = 0
            if not stop_distance_pct:
                stop_distance_pct = 3.0
            return pnl / stop_distance_pct

        equity = peak = max_dd = 0.0
        for t in trades:
            equity += _r_multiple(t)
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)

        # Strategy decay: rolling 20-trade WR, latest vs previous.
        recent20 = pnls[-20:]
        prev20 = pnls[-40:-20]
        recent_wr = sum(1 for p in recent20 if p > 0) / len(recent20) if recent20 else 0
        prev_wr = sum(1 for p in prev20 if p > 0) / len(prev20) if prev20 else 0
        decay_alert = len(prev20) == 20 and recent_wr < prev_wr - 0.15

        return {
            "profit_factor": profit_factor,
            "max_drawdown_pct": round(max_dd, 2),
            "max_drawdown_label": "Max DD (1% risk/trade)",
            "rolling_wr_recent20": round(recent_wr * 100, 1),
            "rolling_wr_prev20": round(prev_wr * 100, 1),
            "decay_alert": decay_alert,
            "trend": ("IMPROVING" if recent_wr > prev_wr + 0.05
                      else "DECAYING" if decay_alert else "STABLE"),
        }

    def should_pause_trading(self) -> tuple[bool, str]:
        s = self.get_summary()
        if s["consecutive_losses"] >= 3:
            return True, "3 consecutive losses — cool-down recommended"
        if s["total_pnl_inr"] < -(self.starting_capital * 0.15):
            return True, "Portfolio down >15%"
        return False, ""
