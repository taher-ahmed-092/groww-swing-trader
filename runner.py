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
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
IST = ZoneInfo(TZ)


_PAUSE_FILE = os.path.join("data", "cache", "trading_paused.txt")
_LOCK_FILE = os.path.join("data", "cache", "runner.lock")


def _kill_switch() -> bool:
    return Path(LIMITS.kill_switch_file).exists()


def _is_paused() -> bool:
    return Path(_PAUSE_FILE).exists()


def _acquire_process_lock() -> bool:
    """Prevent a second runner instance. Dependency-free PID liveness check."""
    try:
        if os.path.exists(_LOCK_FILE):
            old = int(Path(_LOCK_FILE).read_text().strip() or "0")
            if old and old != os.getpid():
                try:
                    os.kill(old, 0)  # raises if the PID is dead
                    return False     # another live runner holds the lock
                except OSError:
                    pass             # stale lock — take it over
        os.makedirs(os.path.dirname(_LOCK_FILE), exist_ok=True)
        Path(_LOCK_FILE).write_text(str(os.getpid()))
        return True
    except OSError:
        return True  # never block startup on lock IO errors


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
    if _kill_switch() or _is_paused():
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
    if _kill_switch() or _is_paused():
        return
    console.print("[cyan][JOB] daily_premarket_job starting[/cyan]")
    notifier = TelegramNotifier()

    # Reliability: skip closed days; warn on an expired Groww token (live mode).
    from src.data.market_calendar import NSECalendar

    if not NSECalendar().is_market_open():
        console.print("[yellow][JOB] market closed today — skipping[/yellow]")
        return
    if settings.live_trading_enabled:
        from src.broker.token_manager import GrowwTokenManager

        if not GrowwTokenManager().check_token_health().get("healthy"):
            notifier.send_message("⚠️ Groww token unhealthy — skipping live premarket job.")
            return

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

    # Forward-simulation learning loop: fill 7/14-day outcomes, promote insights.
    try:
        from src.learning.forward_simulator import ForwardSimulator
        from src.memory.journal import KnowledgeEntry

        sim = ForwardSimulator(journal)
        results = sim.fill_simulation_outcomes()
        if results["updated"] > 0:
            console.print(f"[cyan][JOB] filled {results['updated']} sim outcomes: "
                          f"{results['wins']}W {results['losses']}L[/cyan]")
            for key, insight in sim.get_simulation_insights().items():
                if isinstance(insight, dict) and insight.get("count", 0) >= 10:
                    journal.log_knowledge_entry(KnowledgeEntry(
                        pattern_id=f"sim-{key}",
                        pattern_description=insight["label"],
                        category="SIMULATION_LEARNING",
                        confidence=min(0.3 + insight.get("win_rate", 0.5) * 0.5, 0.9),
                        observed_count=insight["count"],
                        is_hypothesis=insight["count"] < 20,
                        last_seen_in_trade="forward-sim",
                    ))
    except Exception as exc:
        console.print(f"[yellow][JOB] forward-sim fill failed: {exc}[/yellow]")

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

    from src.memory.adaptive_thresholds import AdaptiveThresholds

    changes = AdaptiveThresholds().update_from_all_trades()
    if changes:
        change_lines = []
        for regime, c in changes.items():
            direction = "↓ easier" if c["new"] < c["old"] else "↑ harder"
            change_lines.append(f"{regime}: {c['old']}→{c['new']} ({direction}, WR={c['win_rate']})")
        console.print(f"[cyan][JOB] adaptive thresholds updated: {changes}[/cyan]")
        TelegramNotifier().send_message(
            "🧠 *System Self-Improved*\n"
            "Thresholds adjusted from trading evidence:\n" + "\n".join(change_lines)
        )

    from src.analytics.performance import PerformanceAnalyzer

    metrics = PerformanceAnalyzer().get_professional_metrics()
    if metrics.get("decay_alert"):
        console.print(f"[yellow][JOB] strategy decay detected: {metrics}[/yellow]")
        TelegramNotifier().send_message(
            "⚠️ *STRATEGY DECAY DETECTED*\n"
            f"Rolling 20-trade WR fell from {metrics['rolling_wr_prev20']}% to "
            f"{metrics['rolling_wr_recent20']}%.\n"
            "The adaptive thresholds will tighten automatically. "
            "Consider reviewing LESSONS.md for what changed."
        )

    from src.memory.auto_rules import AutoRuleExtractor

    rules = AutoRuleExtractor().extract_and_save()
    console.print(f"[cyan][JOB] Auto-rules updated: {rules['total_rules']} rules[/cyan]")
    if rules["total_rules"] > 0:
        veto_count = len(rules["stock_vetoes"])
        boost_count = len(rules["stock_boosts"])
        TelegramNotifier().send_message(
            "🤖 *Auto-rules updated from learning*\n"
            f"Stocks to avoid: {veto_count}\n"
            f"Stocks to prefer: {boost_count}\n"
            f"Setup patterns: {len(rules['setup_vetoes'])} vetoes, "
            f"{len(rules['setup_boosts'])} boosts\n"
            f"Total active rules: {rules['total_rules']}"
        )

    from src.ml.stock_priors import StockPriors

    StockPriors().compute_and_save()
    console.print("[cyan][JOB] Stock priors recomputed[/cyan]")

    _run_engine_scorecard()


def _run_engine_scorecard() -> None:
    """Meta-learning: throttle/pause/restore each learning engine based on
    its own realized profit factor. Called from daily_learning_job (4:30 PM)
    and nightly_engine_scorecard_job (11 PM)."""
    from src.analytics.strategy_scorecard import EngineScorecard

    changes = EngineScorecard().apply_throttles()
    if changes:
        lines = ["🧠 *Self-adjustment*"]
        for engine, c in changes.items():
            icon = {"paused": "⏸", "throttled": "🐢", "normal": "▶️"}.get(c["new"], "")
            lines.append(f"{icon} {engine} {c['old']} -> {c['new']} ({c['reason']})")
        console.print(f"[yellow][JOB] Engine scorecard changes: {changes}[/yellow]")
        TelegramNotifier().send_message("\n".join(lines))
    else:
        console.print("[dim][JOB] Engine scorecard: no changes[/dim]")


def nightly_engine_scorecard_job() -> None:
    """11 PM IST — second daily scorecard pass (in addition to daily_learning_job's
    4:30 PM run) so engine throttling reacts within the same trading day."""
    if _kill_switch():
        return
    _run_engine_scorecard()


def weekly_distillation_job() -> None:
    if _kill_switch():
        return
    console.print("[cyan][JOB] weekly_distillation_job starting[/cyan]")
    _run_weekly_distillation_once()  # sends the Telegram learning summary internally


def weekly_model_retrain_job() -> None:
    """Retrain the XGBoost signal combiner on accumulated trades (Sunday 9 PM IST)."""
    if _kill_switch():
        return
    console.print("[cyan][JOB] weekly_model_retrain_job starting[/cyan]")
    from src.ml.signal_combiner import SignalCombiner

    result = SignalCombiner().train()
    if result.get("trained"):
        top = ", ".join(f"{f}" for f, _ in result.get("top_features", []))
        TelegramNotifier().send_message(
            f"🤖 Signal model retrained on {result['n_samples']} trades.\n"
            f"Top predictors: {top}"
        )
    else:
        console.print(f"[yellow][JOB] model not retrained: {result.get('message', '')}[/yellow]")


def saturday_batch_job() -> None:
    """Submit the weekly fundamental batch (Saturday 7 PM IST) — ~50% cheaper."""
    if _kill_switch():
        return
    from src.batch.weekly_research_batch import WeeklyResearchBatch

    candidates = ScoutAgent().scan()
    batch_id = WeeklyResearchBatch().submit_weekly_batch(candidates)
    if batch_id:
        TelegramNotifier().send_message(f"📦 {len(candidates)} stocks queued for batch analysis.")


def sunday_morning_batch_check() -> None:
    """Retrieve weekly batch results (Sunday 7 AM IST)."""
    if _kill_switch():
        return
    from src.batch.weekly_research_batch import WeeklyResearchBatch

    results = WeeklyResearchBatch().check_and_retrieve_results()
    notifier = TelegramNotifier()
    if results:
        notifier.send_message(f"✅ Batch ready: {len(results)} fundamentals cached for the week.")
    else:
        notifier.send_message("⏳ Weekly batch still processing (or none submitted).")


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


def intraday_entry_job() -> None:
    """Mon-Fri 9:30 AM — open 3-5 intraday learning simulations on real NSE data."""
    from src.risk.checker import is_daily_loss_halted

    if _kill_switch() or _is_paused() or is_daily_loss_halted():
        return
    from src.learning.intraday_simulator import IntradaySimulator

    sims = IntradaySimulator().run_morning_entries()
    for s in sims:
        _broadcast_trade_event_safe("TRADE_OPENED", {**s, "source": "intraday"})
    if sims:
        names = ", ".join(s["symbol"] for s in sims)
        TelegramNotifier().send_message(
            "🔬 *Daily Learning Started*\n"
            f"Simulating: {names}\n"
            f"_{len(sims)} positions open — results at 3:15 PM_"
        )
    console.print(f"[cyan][JOB] intraday_entry_job opened {len(sims)} sims[/cyan]")


def intraday_exit_job() -> None:
    """Mon-Fri 3:15 PM — close sims, compute P&L, update the knowledge base."""
    if _kill_switch():
        return
    from src.learning.intraday_simulator import IntradaySimulator

    sim = IntradaySimulator()
    result = sim.run_afternoon_exits()
    for t in result.get("results", []):
        _broadcast_trade_event_safe("TRADE_CLOSED", {**t, "source": "intraday"})
    n = result["closed"]
    if n > 0:
        wins, losses = result["wins"], result["losses"]
        emoji = "🎉" if wins > losses else "📚"
        TelegramNotifier().send_message(
            f"{emoji} *Daily Learning Complete*\n"
            f"{wins}W / {losses}L from {n} simulations\n"
            "Knowledge base updated.\n"
            f"Total simulations: {sim.get_summary()['total']}"
        )
    console.print(f"[cyan][JOB] intraday_exit_job closed {n} sims[/cyan]")


def _run_guarantee(label: str, target: int) -> None:
    """Shared daily-guarantee runner. Takes the best available trade through the
    FULL pipeline (judge + risk + stop) if below `target`; notifies on Telegram."""
    if _kill_switch() or _is_paused():
        return
    from src.trading.daily_guarantee import DailyTradeGuarantee

    taken = DailyTradeGuarantee().ensure_minimum_trades(target=target)
    notifier = TelegramNotifier()
    for t in taken:
        if t.get("outcome") == "executed":
            notifier.send_message(
                f"📚 Learning trade placed: {t['symbol']} ({t['strategy']})\n"
                "Paper trade for daily learning — full pipeline (judge + risk + stop)."
            )
        elif t.get("outcome") == "simulated":
            notifier.send_message(
                f"🔬 No setup cleared the judge today — logged {t['symbol']} "
                f"({t['strategy']}) as a simulation so the system still learns."
            )
    console.print(f"[cyan][JOB] {label}: {len(taken)} guarantee action(s)[/cyan]")


def intraday_scan_job() -> None:
    """Mon-Fri every 30 min, 9:00 AM-2:30 PM IST — the #1 audit priority: get the
    first real pipeline trade (judge + risk + stop, not a forced simulation).
    Reuses DailyTradeGuarantee (pairs-first, then oversold mean-reversion) rather
    than duplicating pipeline code — this just calls it far more often than the
    existing 3x/day guarantee jobs so a qualifying setup is caught sooner."""
    _run_guarantee("intraday_scan", target=999)


def midmorning_guarantee_job() -> None:
    """Mon-Fri 10:30 AM — ensure at least 1 paper trade today (MIN)."""
    _run_guarantee("midmorning_guarantee", target=1)


def midday_guarantee_job() -> None:
    """Mon-Fri 12:30 PM — top up toward the daily target (3)."""
    _run_guarantee("midday_guarantee", target=3)


def afternoon_guarantee_job() -> None:
    """Mon-Fri 2:00 PM — last chance to guarantee a trade before 3 PM (MIN)."""
    _run_guarantee("afternoon_guarantee", target=1)


def hourly_health_job() -> None:
    """Hourly heartbeat — verify the journal is readable; alert on failure only."""
    if _kill_switch():
        return
    try:
        TradingJournal().get_recent(n=1)
        console.print("[dim][JOB] hourly_health_job ok[/dim]")
    except Exception as exc:
        TelegramNotifier().send_message(f"⚠️ System health check failed: {exc}")
        console.print(f"[red][JOB] hourly_health_job DB error: {exc}[/red]")


def off_hours_replay_job() -> None:
    """Every 2 hours, 24/7 — replay historical days to grow the knowledge base.
    Lighter batch during market hours (real scans take priority), full at night."""
    if _kill_switch():
        return
    from src.learning.historical_replay import HistoricalReplayEngine

    now_ist = datetime.now(IST)
    h, m = now_ist.hour, now_ist.minute
    market_open = (9 <= h < 15) or (h == 15 and m <= 30)
    engine = HistoricalReplayEngine()
    if market_open:
        result = engine.run_batch(n_stocks=20, n_days_each=3)
    else:
        result = engine.run_batch(n_stocks=30, n_days_each=5)

    console.print(
        f"[cyan][JOB] replay: {result['replayed']} stocks, {result['signals_found']} signals, "
        f"{result['wins']}W/{result['losses']}L, {result['patterns_added']} new patterns[/cyan]")

    if result.get("replayed"):
        _broadcast_squadron_safe(result["replayed"], result["wins"], result["losses"])

    # Telegram digest only at 8 AM / 2 PM / 8 PM and only when meaningful.
    if result["patterns_added"] >= 3 and now_ist.hour in (8, 14, 20):
        stats = engine.get_stats()
        TelegramNotifier().send_message(
            "🎓 *Overnight Learning Update*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Replayed: {result['replayed']} stocks\n"
            f"Signals found: {result['signals_found']}\n"
            f"Outcomes: {result['wins']}W / {result['losses']}L\n"
            f"New patterns: {result['patterns_added']}\n\n"
            f"📚 Total replay patterns: {stats['replay_patterns_in_kb']}\n"
            f"Stocks remaining to replay: {stats['stocks_pending']}\n\n"
            "_The system never stops learning._")


def deep_replay_job() -> None:
    """Nightly 11:30 PM IST — deeper replay (more days per stock)."""
    if _kill_switch():
        return
    from src.learning.historical_replay import HistoricalReplayEngine

    result = HistoricalReplayEngine().run_batch(n_stocks=50, n_days_each=15)
    console.print(f"[cyan][JOB] deep replay: {result}[/cyan]")
    daily_lessons_job()
    rf_train_job()


def rf_train_job() -> None:
    """Sunday 9:30 PM IST (also called after deep_replay_job, which is when
    enough fresh simulation data exists) — train the Random Forest model."""
    if _kill_switch():
        return
    from src.ml.random_forest_model import RandomForestModel

    result = RandomForestModel().train()
    if result.get("trained"):
        imp = result.get("top_features", [])
        top_f = ", ".join(f"{f}={v}" for f, v in imp[:3])
        console.print(f"[cyan][JOB] Random Forest trained: {result}[/cyan]")
        TelegramNotifier().send_message(
            "🌲 *Random Forest Updated*\n"
            f"Trained on {result['n_samples']} sim trades\n"
            f"Validation accuracy: {result['val_accuracy']:.0%}\n"
            f"Key signals: {top_f}\n"
            "_(Combined with judge for better decisions)_"
        )
    else:
        console.print(f"[yellow][JOB] Random Forest not trained: {result}[/yellow]")


def daily_lessons_job() -> None:
    """11:00 PM IST (also called after deep_replay_job) — refresh LESSONS.md."""
    if _kill_switch():
        return
    from src.memory.lessons_writer import LessonsWriter

    path = LessonsWriter().write()
    console.print(f"[cyan][JOB] LESSONS.md updated: {path}[/cyan]")


_LAST_FORCED_NOTIFY = Path("data/cache/last_forced_notify.txt")


def _should_notify_forced() -> bool:
    """File-based rate limit — at most one 'trades placed' Telegram message/hour."""
    try:
        if not _LAST_FORCED_NOTIFY.exists():
            return True
        last = float(_LAST_FORCED_NOTIFY.read_text() or 0)
        return (time.time() - last) > 3600
    except OSError:
        return True


def _mark_notified_forced() -> None:
    try:
        _LAST_FORCED_NOTIFY.parent.mkdir(parents=True, exist_ok=True)
        _LAST_FORCED_NOTIFY.write_text(str(time.time()))
    except OSError:
        pass


def _broadcast_trade_event_safe(event_type: str, trade: dict) -> None:
    """Push a live trade event to the dashboard. No-op if the dashboard process
    isn't running — this runner and the dashboard are separate processes."""
    try:
        import asyncio

        from dashboard.server import broadcast_trade_event

        asyncio.run(broadcast_trade_event(event_type, trade))
    except Exception:
        pass


def _broadcast_squadron_safe(count: int, wins: int, losses: int) -> None:
    """One combined 'squadron' event per replay batch — never one event per
    stock, which would spam 15-50 plane launches at once."""
    try:
        import asyncio

        from dashboard.server import broadcast_trade_event

        asyncio.run(broadcast_trade_event("SQUADRON", {
            "source": "replay", "count": count, "wins": wins, "losses": losses}))
    except Exception:
        pass


def forced_trade_job() -> None:
    """Every 15 minutes, all day, 24/7 — place forced learning trades. No hour
    restriction: market hours score+place live candidates, off-hours run
    historical simulations. Volume, not timing, is the point."""
    if _kill_switch() or _is_paused():
        return
    from src.trading.always_on_trader import AlwaysOnTrader

    trades = AlwaysOnTrader().ensure_daily_trades()
    for t in trades:
        console.print(f"[cyan][FORCED] {t['symbol']} entry=₹{t['entry']:.2f} "
                      f"stop=₹{t['stop']:.2f} score={t['signal_score']:.2f}[/cyan]")
        t.setdefault("source", "forced")
        _broadcast_trade_event_safe(
            "TRADE_CLOSED" if t.get("outcome") not in (None, "OPEN") else "TRADE_OPENED", t)
    if trades and _should_notify_forced():
        lines = [f"⚡ *{len(trades)} learning trades*"]
        for t in trades:
            lines.append(f"📊 {t['symbol']} ₹{t['entry']:.0f} → stop ₹{t['stop']:.0f}")
        TelegramNotifier().send_message("\n".join(lines))
        _mark_notified_forced()


def forced_close_job() -> None:
    """Every 15 minutes, all day, 24/7 (offset from forced_trade_job) — close
    forced trades that have hit the 90-min hold mark (checked frequently so
    the close happens promptly once the mark is crossed)."""
    if _kill_switch():
        return
    from src.trading.always_on_trader import AlwaysOnTrader

    closed = AlwaysOnTrader().close_open_forced_trades()
    if closed:
        wins = sum(1 for t in closed if t["outcome"] == "WIN")
        console.print(f"[cyan][FORCED CLOSE] {len(closed)} trades: "
                      f"{wins}W/{len(closed) - wins}L[/cyan]")
        for t in closed:
            t.setdefault("source", "forced")
            _broadcast_trade_event_safe("TRADE_CLOSED", t)


def continuous_sim_job() -> None:
    """Every 30 minutes, 24/7 — unlimited historical-data paper simulations.
    Runs regardless of market hours; during market hours it augments
    forced_trade_job's live activity, off-hours it's the only source of new
    learning data (src/learning/continuous_simulator.py)."""
    if _kill_switch():
        return
    from src.learning.continuous_simulator import ContinuousSimulator

    result = ContinuousSimulator().run_batch()
    if result["simulated"] > 0:
        console.print(
            f"[cyan][CONT-SIM] {result['simulated']} sims: "
            f"{result['wins']}W/{result['losses']}L, "
            f"{result['new_patterns']} new patterns[/cyan]")


def drift_check_job() -> None:
    """Every 6 hours — detect win-rate/pattern/regime drift and auto-adapt
    (confidence decay + auto-rule regeneration) if found."""
    if _kill_switch():
        return
    from src.analytics.drift_detector import DriftDetector

    results = DriftDetector().check_and_respond()
    if results.get("any_drift"):
        wr, pat, reg = results["wr_drift"], results["pattern_drift"], results["regime_drift"]
        parts = []
        if wr.get("detected"):
            parts.append(f"WR: {wr['message']}")
        if pat.get("detected"):
            drifted = pat.get("drifted_count", 0)
            example = (pat.get("patterns") or [{}])[0]
            old_conf = example.get("old_confidence", 0)
            loss_rate = example.get("recent_loss_rate", 0)
            parts.append(
                f"{drifted} patterns were {old_conf:.0%} confident but now "
                f"failing {loss_rate:.0%} of the time. Confidence decayed — "
                "system re-learning."
            )
        if reg.get("detected"):
            parts.append(f"Regime: {reg['message']}")
        console.print(f"[yellow][JOB] Concept drift detected: {parts}[/yellow]")
        TelegramNotifier().send_message(
            "⚠️ *Concept Drift Detected*\n" + "\n".join(parts) + "\n"
            "Auto-adapting: confidence decayed, rules regenerated."
        )
    else:
        console.print("[dim][JOB] Drift check: stable[/dim]")


def triday_health_check_job() -> None:
    """Every 3 days at 8:00 PM IST — full system health check, reported to
    Telegram (scripts/system_health_check.py sends its own report)."""
    if _kill_switch():
        return
    import subprocess

    try:
        subprocess.run([sys.executable, "scripts/system_health_check.py"],
                       cwd=".", timeout=120)
    except Exception as exc:
        console.print(f"[yellow][JOB] health check failed: {exc}[/yellow]")


def daily_forced_summary_job() -> None:
    """Mon-Fri 4:00 PM IST — one daily learning summary instead of per-close spam."""
    if _kill_switch():
        return
    from src.learning.historical_replay import HistoricalReplayEngine
    from src.trading.always_on_trader import AlwaysOnTrader

    summary = AlwaysOnTrader().get_todays_summary()
    try:
        kb_count = HistoricalReplayEngine().get_stats().get("replay_patterns_in_kb", 0)
    except Exception:
        kb_count = 0
    TelegramNotifier().send_message(
        "📊 *Daily Learning Summary*\n"
        f"Forced trades: {summary['total']}\n"
        f"{summary['wins']}W / {summary['losses']}L\n"
        f"Win rate: {summary['win_rate']:.0f}%\n"
        f"KB patterns: {kb_count}")


# ── Main ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not _acquire_process_lock():
        console.print("[bold red]Another runner instance is already running — exiting.[/bold red]")
        sys.exit(1)
    console.print(f"[bold green]🚀 groww-swing-trader running | Mode: {settings.broker_mode}[/bold green]")
    console.print(
        "[green]Scheduler active. Jobs: weekly scan (Sun 7pm), daily check (Mon-Fri 9am), "
        "intraday learning (Mon-Fri 9:30am open / 3:15pm close), "
        "postmarket (Mon-Fri 4pm), daily learning (Mon-Fri 4:30pm), "
        "weekly distillation (Sun 8pm), agent evaluation + enhancer (Sun 8:30pm)[/green]"
    )
    console.print("[green]One command: uv run python scripts/start_everything.py[/green]")
    console.print("[green]Dashboard only: uv run python scripts/run_dashboard.py[/green]")
    console.print("[green]Verify setup: uv run python scripts/verify_setup.py[/green]")
    console.print("[green]Press Ctrl+C to stop. KILL_SWITCH file halts all jobs immediately.[/green]")

    try:
        kb = TradingJournal().get_active_knowledge(min_confidence=0.0)
        from src.memory.auto_rules import AutoRuleExtractor

        rules = AutoRuleExtractor.load()
        n_rules = (len(rules.get("stock_vetoes", {})) + len(rules.get("stock_boosts", {}))
                  + len(rules.get("setup_vetoes", [])) + len(rules.get("setup_boosts", [])))
        max_conf = max((e.confidence for e in kb), default=0.0)
        console.print(f"[cyan]KB: {len(kb)} patterns | Auto-rules: {n_rules} | "
                      f"Max conf: {max_conf:.0%}[/cyan]")
    except Exception as exc:
        console.print(f"[yellow]KB status unavailable: {exc}[/yellow]")

    scheduler = BackgroundScheduler(timezone=TZ)
    scheduler.add_job(weekly_research_job, "cron", day_of_week="sun", hour=19, minute=0)
    scheduler.add_job(daily_premarket_job, "cron", day_of_week="mon-fri", hour=9, minute=0)
    scheduler.add_job(daily_learning_job, "cron", day_of_week="mon-fri", hour=16, minute=30)
    scheduler.add_job(daily_postmarket_job, "cron", day_of_week="mon-fri", hour=16, minute=0)
    scheduler.add_job(weekly_distillation_job, "cron", day_of_week="sun", hour=20, minute=0)
    scheduler.add_job(weekly_agent_evaluation_job, "cron", day_of_week="sun", hour=20, minute=30)
    scheduler.add_job(weekly_model_retrain_job, "cron", day_of_week="sun", hour=21, minute=0)
    scheduler.add_job(saturday_batch_job, "cron", day_of_week="sat", hour=19, minute=0)
    scheduler.add_job(sunday_morning_batch_check, "cron", day_of_week="sun", hour=7, minute=0)
    scheduler.add_job(intraday_entry_job, "cron", day_of_week="mon-fri", hour=9, minute=30)
    scheduler.add_job(intraday_exit_job, "cron", day_of_week="mon-fri", hour=15, minute=15)
    scheduler.add_job(intraday_scan_job, "cron", day_of_week="mon-fri", hour="9-14", minute="*/30")
    scheduler.add_job(midmorning_guarantee_job, "cron", day_of_week="mon-fri", hour=10, minute=30)
    scheduler.add_job(midday_guarantee_job, "cron", day_of_week="mon-fri", hour=12, minute=30)
    scheduler.add_job(afternoon_guarantee_job, "cron", day_of_week="mon-fri", hour=14, minute=0)
    scheduler.add_job(hourly_health_job, "cron", minute=0)
    scheduler.add_job(off_hours_replay_job, "cron", hour="*/2", minute=15)
    scheduler.add_job(deep_replay_job, "cron", hour=23, minute=30)
    scheduler.add_job(daily_lessons_job, "cron", hour=23, minute=0)
    scheduler.add_job(rf_train_job, "cron", day_of_week="sun", hour=21, minute=30)
    scheduler.add_job(forced_trade_job, "cron", minute="*/15")
    scheduler.add_job(forced_close_job, "cron", minute="7,22,37,52")
    scheduler.add_job(daily_forced_summary_job, "cron", day_of_week="mon-fri", hour=16, minute=0)
    scheduler.add_job(continuous_sim_job, "cron", minute="*/30")
    scheduler.add_job(drift_check_job, "cron", hour="*/6")
    scheduler.add_job(triday_health_check_job, "cron", day="*/3", hour=20, minute=0)
    scheduler.add_job(nightly_engine_scorecard_job, "cron", hour=23, minute=15)
    scheduler.start()

    # Telegram connectivity check — sends a hello message if configured.
    notifier = TelegramNotifier()
    tg_result = notifier.test_connection()
    if tg_result["ok"]:
        console.print(f"[green]✅ Telegram: connected as {tg_result['bot_name']}[/green]")
    else:
        console.print(f"[yellow]⚠️  Telegram: {tg_result['reason']}[/yellow]")

    # Telegram command handler runs in a background thread (answers /commands).
    if settings.telegram_bot_token:
        import threading

        from src.notifications.command_handler import CommandHandler

        threading.Thread(target=CommandHandler().start, daemon=True,
                         name="telegram-commands").start()
        console.print("[green]Telegram command handler started.[/green]")

    try:
        while True:
            time.sleep(60)
            if Path(LIMITS.kill_switch_file).exists():
                console.print("[bold red]KILL_SWITCH detected — halting scheduler.[/bold red]")
                scheduler.shutdown()
                break
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
