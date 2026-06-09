"""
Automation runner — the continuous entry point for groww-swing-trader.

APScheduler (Asia/Kolkata) drives three jobs:
  • weekly_research_job   Sun 19:00 IST — scout + fundamental + hard-reject screen
  • daily_premarket_job   Mon-Fri 09:00 IST — technical + judge + risk + execute
  • daily_postmarket_job  Mon-Fri 16:00 IST — close hit stops/targets, reflect, report

Paper mode is the default and needs no credentials. The KILL_SWITCH file halts
everything within a minute. Run:  uv run python runner.py
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from rich.console import Console
from sqlmodel import Session, select

from config.risk_limits import LIMITS
from config.settings import settings
from src.agents.executor.agent import ExecutorAgent
from src.agents.fundamental.agent import FundamentalAgent
from src.agents.meta.workflow_enhancer import WorkflowEnhancer
from src.agents.scout.agent import ScoutAgent
from src.agents.technical.agent import TechnicalAgent
from src.analytics.performance import PerformanceAnalyzer
from src.data.fetcher import MarketDataFetcher
from src.data.market_context import MarketContext
from src.data.screener import ScreenerScraper
from src.evaluation.agent_evaluator import AgentEvaluator
from src.judge.evaluator import LLMJudge
from src.memory.daily_synthesis import DailySynthesizer
from src.memory.journal import TradeRecord, TradingJournal
from src.memory.rca import RootCauseAnalyzer
from src.memory.reflection import PostTradeReflector
from src.memory.weekly_distillation import WeeklyDistiller
from src.notifications.telegram_bot import TelegramNotifier
from src.orchestrator.state import get_initial_state
from src.risk.checker import RiskChecker

console = Console()

_CANDIDATES_FILE = os.path.join("data", "cache", "weekly_candidates.json")
_DISTILL_MARKER = os.path.join("data", "cache", "last_distill.txt")
TZ = "Asia/Kolkata"


def _kill_switch() -> bool:
    return Path(LIMITS.kill_switch_file).exists()


def _run_weekly_distillation_once() -> None:
    """Run WeeklyDistiller at most once per calendar day.

    Both weekly_research_job (7pm Sun) and weekly_distillation_job (8pm Sun) call
    this; the marker prevents a double run, which would otherwise double-confirm
    patterns and inflate confidence scores.
    """
    today = datetime.now().date().isoformat()
    try:
        if os.path.exists(_DISTILL_MARKER):
            with open(_DISTILL_MARKER, encoding="utf-8") as f:
                if f.read().strip() == today:
                    console.print("[yellow][JOB] distillation already ran today — skipping[/yellow]")
                    return
    except OSError:
        pass
    WeeklyDistiller().distill()
    try:
        os.makedirs(os.path.dirname(_DISTILL_MARKER), exist_ok=True)
        with open(_DISTILL_MARKER, "w", encoding="utf-8") as f:
            f.write(today)
    except OSError:
        pass


# ── Jobs ──────────────────────────────────────────────────────────────────────
def weekly_research_job() -> None:
    if _kill_switch():
        return
    console.print("[cyan][JOB] weekly_research_job starting[/cyan]")
    notifier = TelegramNotifier()
    screener = ScreenerScraper()

    # Distill the week's learning into standing knowledge before scanning (Sunday).
    _run_weekly_distillation_once()

    should_pause, reason = PerformanceAnalyzer().should_pause_trading()
    if should_pause:
        notifier.send_message(f"⏸️ Skipping weekly scan — {reason}")
        console.print(f"[yellow][JOB] paused: {reason}[/yellow]")
        return

    candidates = ScoutAgent().scan()
    kept: list[dict] = []
    for cand in candidates:
        symbol, sector = cand["symbol"], cand.get("sector", "")
        state = get_initial_state(symbol)
        state["sector"] = sector
        fundamental = FundamentalAgent().analyze(state)

        data = fundamental.get("data") or {}
        if fundamental.get("hard_rejected") or screener.check_hard_rejects(data):
            console.print(f"[yellow][JOB] discarding {symbol} (hard reject)[/yellow]")
            continue
        kept.append({**cand, "fundamental_verdict": fundamental})

    os.makedirs(os.path.dirname(_CANDIDATES_FILE), exist_ok=True)
    with open(_CANDIDATES_FILE, "w", encoding="utf-8") as f:
        json.dump(kept, f, indent=2, default=str)

    symbols = ", ".join(c["symbol"] for c in kept) or "none"
    notifier.send_message(f"📋 Weekly scan complete. Candidates: {symbols}. Watch these during the week.")
    console.print(f"[green][JOB] weekly scan saved {len(kept)} candidates: {symbols}[/green]")


def daily_premarket_job() -> None:
    if _kill_switch():
        return
    console.print("[cyan][JOB] daily_premarket_job starting[/cyan]")
    notifier = TelegramNotifier()

    if not os.path.exists(_CANDIDATES_FILE):
        console.print("[yellow][JOB] no weekly_candidates.json — skipping[/yellow]")
        return
    with open(_CANDIDATES_FILE, encoding="utf-8") as f:
        candidates = json.load(f)
    if not candidates:
        console.print("[yellow][JOB] empty candidate list — skipping[/yellow]")
        return

    ctx = MarketContext().get_nifty_context()
    if not ctx.get("market_safe_to_buy"):
        notifier.send_message(f"⚠️ Nifty in {ctx.get('nifty_trend')} — skipping today's scans.")
        console.print(f"[yellow][JOB] {ctx.get('context_summary')} — skipping[/yellow]")
        return

    for cand in candidates:
        symbol = cand["symbol"]
        state = get_initial_state(symbol)
        state["sector"] = cand.get("sector", "")
        state["market_context"] = ctx
        state["fundamental_verdict"] = cand.get("fundamental_verdict", {})

        technical = TechnicalAgent().analyze(state)
        state["technical_verdict"] = technical
        if not (technical.get("proceed") and (technical.get("score") or 0) >= LIMITS.min_confidence):
            continue

        judge = LLMJudge().evaluate(state)
        state["judge_verdict"] = judge
        if not judge.get("approved"):
            continue

        risk = RiskChecker().check(state)
        state["risk_check"] = risk
        if not risk.get("approved"):
            continue

        state["trade_decision"] = {
            "symbol": symbol,
            "order_type": "BUY",
            "quantity": risk["quantity"],
            "price": technical["entry_price"],
            "stop_price": technical["stop_price"],
            "target_price": technical["target_price"],
        }
        notifier.send_trade_card(state)
        result = ExecutorAgent().execute(state)
        console.print(f"[green][JOB] {symbol} → {result.get('status')}[/green]")


def daily_postmarket_job() -> None:
    if _kill_switch():
        return
    console.print("[cyan][JOB] daily_postmarket_job starting[/cyan]")
    journal = TradingJournal()
    fetcher = MarketDataFetcher()
    reflector = PostTradeReflector(journal)
    analyzer = RootCauseAnalyzer(journal)
    notifier = TelegramNotifier()

    with Session(journal.engine) as session:
        open_trades = list(session.exec(select(TradeRecord).where(TradeRecord.outcome == "OPEN")).all())

    console.print(f"[cyan][JOB] {len(open_trades)} open positions[/cyan]")
    for trade in open_trades:
        price = fetcher.get_current_price(trade.symbol)
        if price is None:
            continue
        hit_stop = trade.stop_price is not None and price <= trade.stop_price
        hit_target = trade.target_price is not None and price >= trade.target_price
        if not (hit_stop or hit_target):
            continue

        closed = journal.log_closed(trade.id, price)
        reflector.reflect(closed)
        console.print(
            f"[magenta][JOB] closed {trade.symbol} @ {price} "
            f"({'TARGET' if hit_target else 'STOP'}) → {closed.outcome}[/magenta]"
        )

        # Recover the entry-time snapshot and run learning hooks.
        try:
            snapshot = json.loads(closed.state_snapshot) if closed.state_snapshot else {}
        except (json.JSONDecodeError, TypeError):
            snapshot = {}
        try:
            if closed.outcome == "LOSS":
                analyzer.analyze(closed, snapshot)  # categorize + extract lesson
            elif closed.outcome == "WIN":
                analyzer.analyze_win(closed, snapshot)  # confirm standing patterns
        except Exception as exc:
            console.print(f"[yellow][JOB] RCA failed for {trade.symbol}: {exc}[/yellow]")

    if datetime.now().weekday() == 4:  # Friday
        notifier.send_message(PerformanceAnalyzer().get_weekly_report())


def daily_learning_job() -> None:
    if _kill_switch():
        return
    console.print("[cyan][JOB] daily_learning_job starting[/cyan]")
    entry = DailySynthesizer().synthesize()
    try:
        patterns = json.loads(entry.patterns_observed) if entry.patterns_observed else []
        lessons = json.loads(entry.lessons_extracted) if entry.lessons_extracted else []
    except (json.JSONDecodeError, TypeError):
        patterns, lessons = [], []
    TelegramNotifier().send_message(
        "📔 Daily Learning\n"
        f"{entry.synthesis}\n"
        f"Patterns: {patterns}\n"
        f"Lessons: {lessons}"
    )


def weekly_distillation_job() -> None:
    if _kill_switch():
        return
    console.print("[cyan][JOB] weekly_distillation_job starting[/cyan]")
    _run_weekly_distillation_once()  # sends the Telegram learning summary internally


def weekly_agent_evaluation_job() -> None:
    """Sunday 8:30 PM — measure agent accuracy + run the self-improvement enhancer."""
    if _kill_switch():
        return
    console.print("[cyan][JOB] weekly_agent_evaluation_job starting[/cyan]")
    notifier = TelegramNotifier()

    results = AgentEvaluator().evaluate_all()
    suggestions = AgentEvaluator().generate_improvement_suggestions(results)

    # Append to the evaluation history.
    eval_file = os.path.join("data", "journal", "agent_evaluations.json")
    try:
        os.makedirs(os.path.dirname(eval_file), exist_ok=True)
        history = []
        if os.path.exists(eval_file):
            with open(eval_file, encoding="utf-8") as f:
                history = json.load(f)
        history.append(results)
        with open(eval_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, default=str)
    except (OSError, json.JSONDecodeError):
        pass

    fund = results.get("fundamental", {})
    tech = results.get("technical", {})
    judge = results.get("judge", {})
    top = suggestions[0] if suggestions else "All agents performing well!"
    notifier.send_message(
        "📊 WEEKLY AGENT EVALUATION\n"
        f"🧠 Fundamental: r={fund.get('score_correlation', 'N/A')}\n"
        f"🔧 Technical: BUY win rate {tech.get('buy_signal_win_rate', 'N/A')}\n"
        f"🧑‍⚖️ Judge calibration error: {judge.get('avg_calibration_error', 'N/A')}\n\n"
        f"💡 TOP SUGGESTION:\n{top}"
    )

    # Self-improvement: the enhancer auto-applies safe parameter changes.
    enhancer = WorkflowEnhancer().run(results)
    applied = enhancer["auto_applied"]
    if applied:
        changes = "\n".join(f"• {a['param']}: {a['old_value']} → {a['new_value']}" for a in applied)
        notifier.send_message(f"🔧 SYSTEM ENHANCEMENT — auto-applied:\n{changes}\nThe system is getting smarter. 🧠")
    else:
        notifier.send_message("🔧 SYSTEM ENHANCEMENT — no auto-changes this week. 📱 Review suggestions in /enhance.")


# ── Main ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    console.print(f"[bold green]🚀 groww-swing-trader running | Mode: {settings.broker_mode}[/bold green]")
    console.print(
        "[green]Scheduler active. Jobs: weekly scan (Sun 7pm), daily check (Mon-Fri 9am), "
        "postmarket (Mon-Fri 4pm), daily learning (Mon-Fri 4:30pm), "
        "weekly distillation (Sun 8pm), agent evaluation + enhancer (Sun 8:30pm)[/green]"
    )
    console.print("[green]Press Ctrl+C to stop. KILL_SWITCH file halts all jobs immediately.[/green]")

    scheduler = BackgroundScheduler(timezone=TZ)
    scheduler.add_job(weekly_research_job, "cron", day_of_week="sun", hour=19, minute=0)
    scheduler.add_job(daily_premarket_job, "cron", day_of_week="mon-fri", hour=9, minute=0)
    scheduler.add_job(daily_learning_job, "cron", day_of_week="mon-fri", hour=16, minute=30)
    scheduler.add_job(daily_postmarket_job, "cron", day_of_week="mon-fri", hour=16, minute=0)
    scheduler.add_job(weekly_distillation_job, "cron", day_of_week="sun", hour=20, minute=0)
    scheduler.add_job(weekly_agent_evaluation_job, "cron", day_of_week="sun", hour=20, minute=30)
    scheduler.start()

    try:
        while True:
            time.sleep(60)
            if Path(LIMITS.kill_switch_file).exists():
                console.print("[bold red]KILL_SWITCH detected — halting scheduler.[/bold red]")
                scheduler.shutdown()
                break
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
