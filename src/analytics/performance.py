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

    def should_pause_trading(self) -> tuple[bool, str]:
        s = self.get_summary()
        if s["consecutive_losses"] >= 3:
            return True, "3 consecutive losses — cool-down recommended"
        if s["total_pnl_inr"] < -(self.starting_capital * 0.15):
            return True, "Portfolio down >15%"
        return False, ""
