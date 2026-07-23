"""
Writes a human-readable LESSONS.md file capturing everything the system has
learned. Updated automatically after each learning cycle.

The user reads this file to understand what the system knows, and can share
it with Claude for analysis and refinement.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.analytics.performance import PerformanceAnalyzer
from src.memory.journal import TradingJournal

IST = ZoneInfo("Asia/Kolkata")


def _is_loss(desc: str) -> bool:
    return "LOST" in desc or "LOSS" in desc


def _is_win(desc: str) -> bool:
    # Regime patterns use "WON"/"LOST"; FORCED_LEARNING patterns use "WIN"/"LOSS"
    # instead — checking only "WON" left every forced-trade WIN entry falling
    # through to the else-branch and rendering a ❌.
    return not _is_loss(desc) and ("WON" in desc or "WIN" in desc)


def _icon_for(desc: str) -> str:
    if _is_loss(desc):
        return "❌"
    if _is_win(desc):
        return "✅"
    return "◆"


class LessonsWriter:
    OUTPUT_FILE = Path("LESSONS.md")

    def write(self) -> Path:
        """Writes the full LESSONS.md. Call after each learning cycle."""
        journal = TradingJournal()
        kb = journal.get_active_knowledge(min_confidence=0.0)
        pa = PerformanceAnalyzer()
        summary = pa.get_summary()

        forced_file = Path("data/cache/forced_trades_history.json")
        forced_all = []
        if forced_file.exists():
            try:
                forced_all = json.loads(forced_file.read_text())
            except Exception:
                pass

        replay_file = Path("data/cache/replay_progress.json")
        replay_progress: dict = {}
        if replay_file.exists():
            try:
                replay_progress = json.loads(replay_file.read_text())
            except Exception:
                pass

        lines = [
            "# Cosmic Punk Trading System — Lessons Learned",
            f"*Auto-generated: {datetime.now(IST).strftime('%d %b %Y %H:%M IST')}*",
            "*Share this file with Claude for analysis and improvement.*",
            "",
            "---",
            "",
            "## System Performance Summary",
            f"- Real pipeline trades: {summary.get('total_trades', 0)}",
            f"- Win rate: {summary.get('win_rate', 0) * 100:.1f}%",
            f"- Total P&L: ₹{summary.get('total_pnl_inr', 0):+.2f}",
            f"- Forced learning trades: {len(forced_all)}",
            "",
            "## Market Regime Observations",
        ]

        regime_patterns: dict = {}
        for e in kb:
            regime = e.observed_in_regime or "UNKNOWN"
            regime_patterns.setdefault(regime, []).append(e)

        for regime, patterns in sorted(regime_patterns.items()):
            wins = [p for p in patterns if _is_win(p.pattern_description)]
            losses = [p for p in patterns if _is_loss(p.pattern_description)]
            lines.append(f"\n### {regime} ({len(wins)} wins, {len(losses)} losses)")
            for p in sorted(patterns, key=lambda x: -x.confidence)[:5]:
                icon = _icon_for(p.pattern_description)
                lines.append(f"- {icon} [{p.confidence:.0%}] {p.pattern_description[:80]}")

        lines += ["", "## What Works (High Confidence Patterns)"]
        high_conf = [e for e in kb if e.confidence >= 0.7 and _is_win(e.pattern_description)]
        if high_conf:
            for e in sorted(high_conf, key=lambda x: -x.confidence)[:10]:
                lines.append(f"- [{e.confidence:.0%}] {e.pattern_description[:80]}")
        else:
            lines.append("- Still accumulating. Need more trades to establish high-confidence rules.")

        lines += ["", "## What Doesn't Work (Loss Patterns to Avoid)"]
        high_loss = [e for e in kb if e.confidence >= 0.6 and _is_loss(e.pattern_description)]
        if high_loss:
            for e in sorted(high_loss, key=lambda x: -x.confidence)[:10]:
                lines.append(f"- [{e.confidence:.0%}] {e.pattern_description[:80]}")
        else:
            lines.append("- Still accumulating.")

        lines += [
            "",
            "## Stocks Analyzed",
            f"- Replay coverage: {len(replay_progress)}/244 stocks",
            f"- Most replayed: {', '.join(list(replay_progress.keys())[:10])}",
            "",
            "## Forced Trade Outcomes by Type",
        ]

        by_type: dict = {}
        for t in forced_all:
            tt = t.get("trade_type", "UNKNOWN")
            by_type.setdefault(tt, {"wins": 0, "total": 0})
            by_type[tt]["total"] += 1
            if t.get("outcome") == "WIN":
                by_type[tt]["wins"] += 1

        for tt, stats in sorted(by_type.items()):
            wr = round(stats["wins"] / stats["total"] * 100)
            lines.append(f"- {tt}: {stats['wins']}W/{stats['total']-stats['wins']}L ({wr}% win rate)")

        lines += [
            "",
            "## Recommendations for Improvement",
            "*(Based on current data — share this section with Claude)*",
            "",
        ]

        recommendations = self._generate_recommendations(kb, forced_all, summary)
        for i, rec in enumerate(recommendations, 1):
            lines.append(f"{i}. {rec}")

        lines += [
            "",
            "---",
            "*This file is auto-updated after every learning cycle.*",
            "*Run `/report` in Telegram for a live summary.*",
        ]

        self.OUTPUT_FILE.write_text("\n".join(lines))
        return self.OUTPUT_FILE

    def _generate_recommendations(self, kb, forced_all, summary) -> list[str]:
        recs = []

        total_trades = summary.get("total_trades", 0)
        if total_trades == 0:
            recs.append(
                "No real pipeline trades yet. Run the system daily during market "
                "hours (9:15-15:30 IST) and use /scan to find opportunities.")

        forced_wins = sum(1 for t in forced_all if t.get("outcome") == "WIN")
        forced_total = len(forced_all)
        if forced_total >= 10:
            wr = forced_wins / forced_total
            if wr < 0.40:
                recs.append(
                    f"Forced trade win rate is {wr:.0%}. Market conditions are unfavorable. "
                    "Consider switching to ROGUE mode to capture more diverse signals.")
            elif wr > 0.60:
                recs.append(
                    f"Forced trade win rate is strong ({wr:.0%}). The market is cooperative. "
                    "Good time to run /scan for higher-quality real trades.")

        loss_patterns = [e for e in kb if _is_loss(e.pattern_description) and e.confidence >= 0.80]
        if loss_patterns:
            top_loss = loss_patterns[0]
            recs.append(
                f"Consistent loss pattern detected: {top_loss.pattern_description[:60]}. "
                "Consider adding this as an auto-veto rule.")

        win_patterns = [e for e in kb if _is_win(e.pattern_description) and e.confidence >= 0.80]
        if win_patterns:
            top_win = win_patterns[0]
            recs.append(
                f"Strong win pattern: {top_win.pattern_description[:60]}. "
                "Consider boosting scout score for this setup.")

        if not recs:
            recs.append(
                "System is accumulating data. Keep running daily. Specific recommendations "
                "will appear after 20+ forced trades.")

        return recs
