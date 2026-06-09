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
from src.agents.scout.agent import ScoutAgent
from src.agents.technical.agent import TechnicalAgent
from src.analytics.performance import PerformanceAnalyzer
from src.data.fetcher import MarketDataFetcher
from src.data.market_context import MarketContext
from src.data.screener import ScreenerScraper
from src.judge.evaluator import LLMJudge
from src.memory.journal import TradeRecord, TradingJournal
from src.memory.reflection import PostTradeReflector
from src.notifications.telegram_bot import TelegramNotifier
from src.orchestrator.state import get_initial_state
from src.risk.checker import RiskChecker

console = Console()

_CANDIDATES_FILE = os.path.join("data", "cache", "weekly_candidates.json")
TZ = "Asia/Kolkata"


def _kill_switch() -> bool:
    return Path(LIMITS.kill_switch_file).exists()


# ── Jobs ──────────────────────────────────────────────────────────────────────
def weekly_research_job() -> None:
    if _kill_switch():
        return
    console.print("[cyan][JOB] weekly_research_job starting[/cyan]")
    notifier = TelegramNotifier()
    screener = ScreenerScraper()

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
        if hit_stop or hit_target:
            closed = journal.log_closed(trade.id, price)
            reflector.reflect(closed)
            console.print(
                f"[magenta][JOB] closed {trade.symbol} @ {price} "
                f"({'TARGET' if hit_target else 'STOP'}) → {closed.outcome}[/magenta]"
            )

    if datetime.now().weekday() == 4:  # Friday
        notifier.send_message(PerformanceAnalyzer().get_weekly_report())


# ── Main ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    console.print(f"[bold green]🚀 groww-swing-trader running | Mode: {settings.broker_mode}[/bold green]")
    console.print(
        "[green]Scheduler active. Jobs: weekly scan (Sun 7pm), "
        "daily check (Mon-Fri 9am), postmarket (Mon-Fri 4pm)[/green]"
    )
    console.print("[green]Press Ctrl+C to stop. KILL_SWITCH file halts all jobs immediately.[/green]")

    scheduler = BackgroundScheduler(timezone=TZ)
    scheduler.add_job(weekly_research_job, "cron", day_of_week="sun", hour=19, minute=0)
    scheduler.add_job(daily_premarket_job, "cron", day_of_week="mon-fri", hour=9, minute=0)
    scheduler.add_job(daily_postmarket_job, "cron", day_of_week="mon-fri", hour=16, minute=0)
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
