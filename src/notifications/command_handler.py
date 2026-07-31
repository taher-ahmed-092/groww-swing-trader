"""
Telegram command handler — a background polling thread that answers /commands.

Sync HTTP (same pattern as TelegramNotifier). Every handler is defensive: a failing
command logs and is skipped, never crashing the loop. Started from runner.py.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from config.risk_limits import LIMITS
from config.settings import settings
from src.notifications.telegram_bot import TelegramNotifier

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"
_PAUSE_FILE = os.path.join("data", "cache", "trading_paused.txt")
_MAX = 4000  # Telegram message limit


def _bar(frac: float, width: int = 10) -> str:
    f = max(0, min(width, round((frac or 0) * width)))
    return "█" * f + "░" * (width - f)


class CommandHandler:
    # Old command → (new flagship command, one-line redirect note). Old commands
    # keep working (never break muscle memory) but point at their replacement.
    _REDIRECTS = {
        "/status": ("/report", "Merged into /report — showing that instead:"),
        "/health": ("/report", "Merged into /report — showing that instead:"),
        "/performance": ("/report", "Merged into /report — showing that instead:"),
        "/totals": ("/report", "Merged into /report — showing that instead:"),
        "/pairs": ("/scan", "Merged into /scan pairs — showing that instead:"),
        "/portfolio": ("/positions", "Merged into /positions — showing that instead:"),
        "/wins": ("/trades", "Merged into /trades wins — showing that instead:"),
        "/lessons": ("/trades", "Merged into /trades losses — showing that instead:"),
        "/knowledge": ("/learn", "Merged into /learn — showing that instead:"),
        "/learning": ("/learn", "Merged into /learn — showing that instead:"),
        "/thresholds": ("/learn", "Merged into /learn — showing that instead:"),
        "/signals": ("/learn", "Merged into /learn — showing that instead:"),
        "/evaluation": ("/learn", "Merged into /learn — showing that instead:"),
        "/lessons_file": ("/learn", "Merged into /learn full — showing that instead:"),
        "/win_chart": ("/chart", "Merged into /chart wins — showing that instead:"),
        "/force_now": ("/trade", "Merged into /trade — showing that instead:"),
        "/forced": ("/trade", "Merged into /trade — showing that instead:"),
        "/resume": ("/pause", "Merged into /pause (toggle) — showing that instead:"),
        "/conserve": ("/mode", "Merged into /mode — use the buttons instead:"),
        "/balanced": ("/mode", "Merged into /mode — use the buttons instead:"),
        "/rogue": ("/mode", "Merged into /mode — use the buttons instead:"),
        "/funds": ("/report", "Merged into /report — showing that instead:"),
        "/dashboard_link": ("/dashboard", "Renamed to /dashboard — showing that instead:"),
        "/enhance": ("/learn", "Merged into /learn — showing that instead:"),
        "/params": ("/learn", "Merged into /learn — showing that instead:"),
        "/strategies": ("/report", "Merged into /report — showing that instead:"),
    }

    def __init__(self) -> None:
        self.tg = TelegramNotifier()
        # FINAL 16-command set surfaced to Telegram's / menu.
        self.commands = {
            "/start": self._start,
            "/report": self._handle_report,
            "/scan": self._cmd_scan,
            "/positions": self._positions,
            "/trades": self._cmd_trades,
            "/learn": self._cmd_learn,
            "/chart": self._cmd_chart,
            "/mode": self._mode,
            "/trade": self._cmd_trade,
            "/regime": self._regime,
            "/brief": self._brief,
            "/watchlist": self._watchlist,
            "/alert": self._alert,
            "/pause": self._cmd_pause,
            "/kill": self._kill,
            "/reset_kill": self._reset_kill,
            "/dashboard": self._dashboard_link,
            "/flows": self._handle_flows,
            "/diagnose": self._handle_diagnose,
            "/health_check": self._handle_health_check,
            "/jobs": self._handle_jobs,
            "/stock": self._handle_stock,
        }
        # Old commands still resolve (never break a typed habit) but redirect.
        for old_cmd in self._REDIRECTS:
            self.commands[old_cmd] = self._make_redirect(old_cmd)
        # Help stays reachable even though it's folded out of the flagship 16.
        self.commands["/help"] = self._help

    # ── lifecycle ──────────────────────────────────────────────────────────────
    # The 16-command final set, in display order, with crisp descriptions.
    _MENU = [
        ("/start", "Welcome + grouped command guide"),
        ("/report", "Full system state: trades, EV, PF, drawdown, thresholds"),
        ("/scan", "Run scan; '/scan pairs' for market-neutral"),
        ("/positions", "Open positions + P&L"),
        ("/trades", "Last 10 closed; '/trades wins'/'losses' to filter"),
        ("/learn", "Learning status: KB, replay, thresholds, XGBoost"),
        ("/chart", "P&L chart; '/chart wins' for win/loss donut"),
        ("/mode", "Show/switch trading mode (buttons)"),
        ("/trade", "Force best-available trade now"),
        ("/regime", "Market regime + strategy advice"),
        ("/brief", "Daily market brief"),
        ("/watchlist", "Show/add/remove stocks"),
        ("/alert", "Price alerts"),
        ("/pause", "Toggle pause/resume trading"),
        ("/kill", "Emergency stop (confirm required)"),
        ("/reset_kill", "Resume after kill switch"),
        ("/dashboard", "Private dashboard link"),
        ("/flows", "Institutional flows + economic calendar"),
        ("/diagnose", "Why real trades aren't clearing the pipeline"),
        ("/health_check", "Full 9-component system health check"),
        ("/jobs", "Scheduled job telemetry: last run, avg duration, failures"),
        ("/stock", "Company dossier for a symbol: /stock SYMBOL"),
    ]

    def setup(self) -> None:
        """Register the / menu in Telegram — ONLY the 16 flagship commands,
        never the old redirected ones (keeps the menu crisp)."""
        if not self.tg.is_configured():
            return
        cmds = [{"command": c.lstrip("/"), "description": d} for c, d in self._MENU]
        try:
            requests.post(_API.format(token=settings.telegram_bot_token, method="setMyCommands"),
                          json={"commands": cmds[:100]}, timeout=10)
        except requests.RequestException:
            pass

    def start(self) -> None:
        """Entry point from runner.py. Wraps run_forever so a crash self-heals
        instead of killing the thread — the bot stays responsive across failures."""
        if not self.tg.is_configured():
            log.debug("Command handler not started — no Telegram credentials.")
            return
        while True:
            try:
                self.run_forever()
            except Exception as exc:  # noqa: BLE001 - never let the thread die
                log.error("Command handler crashed: %s — restarting in 10s", exc)
            time.sleep(10)

    def run_forever(self) -> None:
        if not self.tg.is_configured():
            log.debug("Command handler not started — no Telegram credentials.")
            return
        self.setup()
        offset = None
        while True:
            try:
                # Short long-poll so commands (e.g. /start) feel instant.
                params = {"timeout": 5, "allowed_updates": ["message", "callback_query"]}
                if offset is not None:
                    params["offset"] = offset
                r = requests.get(
                    _API.format(token=settings.telegram_bot_token, method="getUpdates"),
                    params=params, timeout=10)
                for update in r.json().get("result", []):
                    offset = update["update_id"] + 1
                    self._dispatch(update)
            except Exception as exc:
                log.debug("Command poll failed: %s", exc)
                time.sleep(5)

    def _dispatch(self, update: dict) -> None:
        if update.get("callback_query"):
            try:
                self._handle_callback(update["callback_query"])
            except Exception as exc:
                log.debug("Callback handling failed: %s", exc)
            return
        text = (update.get("message") or {}).get("text", "")
        if not text:
            return
        cmd = text.split()[0].split("@")[0].lower()
        args = text.split()[1:]
        handler = self.commands.get(cmd)
        if handler:
            try:
                handler(args)
            except Exception as exc:
                log.debug("Command %s failed: %s", cmd, exc)
                self.tg.send_message(f"⚠️ {cmd} failed: {exc}")

    def _send(self, text: str) -> None:
        self.tg.send_message(text[:_MAX])

    def _make_redirect(self, old_cmd: str):
        """Builds a handler for a retired command: one-line redirect note, then
        runs the new flagship handler with the same args."""
        new_cmd, note = self._REDIRECTS[old_cmd]
        new_handler_name = {
            "/report": "_handle_report", "/scan": "_cmd_scan", "/positions": "_positions",
            "/trades": "_cmd_trades", "/learn": "_cmd_learn", "/chart": "_cmd_chart",
            "/mode": "_mode", "/trade": "_cmd_trade", "/pause": "_cmd_pause",
            "/dashboard": "_dashboard_link",
        }[new_cmd]

        def _redirect(args):
            self._send(note)
            getattr(self, new_handler_name)(args)

        _redirect.__doc__ = f"→ {new_cmd}"
        return _redirect

    # ── handlers ───────────────────────────────────────────────────────────────
    # Emoji + advice per regime, surfaced by /regime.
    REGIME_VISUALS = {
        "BULL_TRENDING": ("🐂", "Full size. Ride the momentum."),
        "BEAR_TRENDING": ("🐻", "Half size. Pairs trading only."),
        "VOLATILE": ("⚡", "Half size. Mean reversion only."),
        "RANGE_BOUND": ("↔️", "Bounces near support. Quick exits."),
        "RECOVERY": ("🌱", "Half size. Wait for MA200 reclaim."),
        "TRANSITIONAL": ("🌀", "Reduced size. Wait for clarity."),
    }

    @staticmethod
    def _stale_suffix(regime: dict) -> str:
        """' (last known · 31 Jul 15:30)' when regime data is a stale cache
        (NSE closed, live fetch returned nothing) — never a bare label, so
        /report can't be mistaken for the system having actually failed."""
        if not regime.get("stale"):
            return ""
        cached_at = regime.get("cached_at")
        return f" (last known · {cached_at})" if cached_at else " (no data yet)"

    def _get_learning_status(self) -> str:
        from src.memory.journal import TradingJournal

        j = TradingJournal()
        recent = j.get_recent(n=200)
        closed = len([t for t in recent if t.outcome in ("WIN", "LOSS")])
        kb = j.get_active_knowledge(min_confidence=0.0)
        return f"{closed} trades · {len(kb)} patterns learned"

    def _start(self, args):
        """welcome + command list"""
        from src.utils.tips import get_random_tip

        # Instant acknowledgment so the bot feels responsive, then the full card.
        self._send("🌌 Loading your trading system…")
        self._send(
            "🌌 *Welcome to Cosmic Punk Trading System*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Hey *{settings.dashboard_owner_name}*! "
            "Your AI trading system is watching Indian markets 24/7.\n\n"
            f"*Mode:* {settings.mode_label}\n"
            "*Engine:* 4 strategies + pairs trading\n"
            f"*Learning:* {self._get_learning_status()}\n\n"
            "📊 *DAILY:* /report /brief /regime\n"
            "⚡ *ACTION:* /scan /trade /positions /mode\n"
            "🧠 *LEARNING:* /learn /trades /chart\n"
            "⚙️ *CONTROL:* /pause /kill /reset_kill /watchlist /alert /dashboard\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 _{get_random_tip()}_"
        )

    def _help(self, args):
        """grouped command list"""
        self._send(
            "*Commands (16 total)*\n"
            "📊 Daily: /report /brief /regime\n"
            "⚡ Action: /scan (pairs) /trade /positions /mode\n"
            "🧠 Learning: /learn (full) /trades (wins/losses) /chart (wins)\n"
            "⚙️ Control: /pause /kill /reset_kill /watchlist /alert /dashboard")

    def _status(self, args):
        """mode, regime, kill switch, positions"""
        from src.analytics.performance import PerformanceAnalyzer
        from src.memory.journal import TradingJournal

        j = TradingJournal()
        s = PerformanceAnalyzer().get_summary()
        kill = "🔴 ACTIVE" if Path(LIMITS.kill_switch_file).exists() else "🟢 off"
        paused = " (paused)" if Path(_PAUSE_FILE).exists() else ""
        self._send(f"*Status*{paused}\nMode: {settings.mode_label}\n"
                   f"Open positions: {len(j.get_open_trades())}\n"
                   f"Closed trades: {s.get('total_trades', 0)} | Win rate: "
                   f"{s.get('win_rate', 0) * 100:.0f}%\nKill switch: {kill}")

    def _positions(self, args):
        """open positions with P&L"""
        from src.data.fetcher import MarketDataFetcher
        from src.memory.journal import TradingJournal

        opens = TradingJournal().get_open_trades()
        if not opens:
            self._send("No open positions. 😴")
            return
        fetcher = MarketDataFetcher()
        lines = ["*Open Positions*"]
        for t in opens:
            price = fetcher.get_current_price(t.symbol) or t.entry_price
            pnl = (price - t.entry_price) / t.entry_price * 100 if t.entry_price else 0
            lines.append(f"{t.symbol}: ₹{t.entry_price:.0f}→₹{price:.0f} "
                         f"({pnl:+.1f}%) {_bar(max(0, min(1, pnl / 20)))}")
        self._send("\n".join(lines))

    def _portfolio(self, args):
        """portfolio summary"""
        from src.analytics.performance import PerformanceAnalyzer

        s = PerformanceAnalyzer().get_summary()
        self._send(f"*Portfolio (paper)*\nClosed P&L: ₹{s.get('total_pnl_inr', 0):.0f}\n"
                   f"Win rate: {s.get('win_rate', 0) * 100:.0f}% | "
                   f"Expectancy: ₹{s.get('expectancy', 0):.1f}")

    def _funds(self, args):
        """available balance"""
        self._send("💰 *Funds*\nPaper mode — virtual capital ₹1500 base.\n"
                   "Add/withdraw real money only via the Groww app (the API cannot "
                   "transfer funds): https://groww.in")

    def _performance(self, args):
        """full performance report"""
        from src.analytics.performance import PerformanceAnalyzer

        pa = PerformanceAnalyzer()
        report = pa.get_weekly_report()
        bd = pa.get_strategy_breakdown()
        extra = "\n".join(
            f"  {n}: {(d['win_rate'] * 100):.0f}% WR ({d['trades']})"
            for n, d in bd.items() if d.get("win_rate") is not None)
        self._send(report + ("\n\n*By strategy*\n" + extra if extra else ""))

    def _trades(self, args):
        """last 10 closed trades"""
        from src.memory.journal import TradingJournal

        closed = [t for t in TradingJournal().get_recent(30) if t.outcome in ("WIN", "LOSS")][:10]
        if not closed:
            self._send("No closed trades yet.")
            return
        lines = ["*Last trades*"] + [
            f"{'✅' if t.outcome == 'WIN' else '❌'} {t.symbol} {t.pnl_pct:+.1f}%"
            for t in closed]
        self._send("\n".join(lines))

    def _wins(self, args):
        """top 5 wins"""
        from src.memory.journal import TradingJournal

        wins = sorted([t for t in TradingJournal().get_recent(50) if t.outcome == "WIN"],
                      key=lambda t: t.pnl_pct or 0, reverse=True)[:5]
        if not wins:
            self._send("No wins yet — they're coming. 💪")
            return
        self._send("🏆 *Top wins*\n" + "\n".join(
            f"🎉 {t.symbol} +{t.pnl_pct:.1f}%" for t in wins))

    def _lessons(self, args):
        """top losses + lessons"""
        from src.memory.journal import TradingJournal

        losses = [t for t in TradingJournal().get_recent(50)
                  if t.outcome == "LOSS" and t.reflection][:5]
        if not losses:
            self._send("No losses logged with lessons yet.")
            return
        self._send("📉 *Lessons*\n" + "\n".join(
            f"{t.symbol} {t.pnl_pct:+.1f}%: {(t.reflection or '')[:120]}" for t in losses))

    def _knowledge(self, args):
        """knowledge base"""
        from src.memory.journal import TradingJournal

        kb = TradingJournal().get_active_knowledge(min_confidence=0.0)[:7]
        if not kb:
            self._send("🧠 Knowledge base empty — learning begins after trades.")
            return
        self._send("🧠 *Knowledge base*\n" + "\n".join(
            f"{_bar(e.confidence, 5)} {e.confidence:.2f} {e.pattern_description[:50]}" for e in kb))

    def _signals(self, args):
        """signal accuracy"""
        from src.tracking.signal_tracker import SignalTracker

        out = SignalTracker().get_best_signals()
        self._send(out or "No signal-accuracy data yet.")

    def _evaluation(self, args):
        """latest agent evaluation"""
        from src.evaluation.agent_evaluator import AgentEvaluator

        r = AgentEvaluator().evaluate_all()
        f = r.get("fundamental", {})
        t = r.get("technical", {})
        self._send(f"📊 *Agent evaluation*\nFundamental r={f.get('score_correlation', 'N/A')}\n"
                   f"Technical BUY WR={t.get('buy_signal_win_rate', 'N/A')}")

    def _enhance(self, args):
        """workflow suggestions"""
        from src.agents.meta.workflow_enhancer import WorkflowEnhancer
        from src.evaluation.agent_evaluator import AgentEvaluator

        res = WorkflowEnhancer().run(AgentEvaluator().evaluate_all())
        if res.get("status") == "waiting":
            self._send(f"🔧 {res.get('message', 'building evidence')}")
            return
        sugg = res.get("suggestions", [])[:3]
        self._send("🔧 *Enhancement suggestions*\n" + "\n".join(
            f"• {s.get('change', '')}" for s in sugg))

    def _params(self, args):
        """adaptive parameters"""
        from src.utils.adaptive import get_adaptive_params

        p = get_adaptive_params()
        self._send("⚙️ *Adaptive params*\n" + "\n".join(f"{k}: {v}" for k, v in p.items()))

    def _regime(self, args):
        """market regime"""
        from src.data.regime_detector import RegimeDetector

        r = RegimeDetector().detect()
        regime = r.get("regime", "UNKNOWN")
        emoji, advice = self.REGIME_VISUALS.get(regime, ("❓", "Unknown — trade cautiously."))
        regime_label = f"{regime}{self._stale_suffix(r)}"
        self._send(
            f"{emoji} *MARKET REGIME: {regime_label}*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 Nifty: ₹{(r.get('nifty_price') or 0):,.0f}\n"
            f"📈 RSI: {(r.get('rsi') or 0):.1f}\n"
            f"📉 MA50: ₹{(r.get('ma50') or 0):,.0f}\n"
            f"📉 MA200: ₹{(r.get('ma200') or 0):,.0f}\n\n"
            f"💡 *Advice:* {advice}\n"
            f"📐 *Position sizing:* {(r.get('size_multiplier') or 1):.0%} capacity\n\n"
            f"_Strategy: {str(r.get('strategy', ''))[:90]}_"
        )

    def _scan(self, args):
        """run a scout scan"""
        from src.agents.scout.agent import ScoutAgent

        self._send("🔍 Scanning…")
        top = ScoutAgent().scan()[:3]
        self._send("*Top candidates*\n" + "\n".join(
            f"{c['symbol']} ({c['score']}) — {c['sector']}" for c in top) if top
            else "No candidates found.")

    def _cmd_scan(self, args):
        """run a scan; '/scan pairs' for market-neutral"""
        if args and args[0].lower() == "pairs":
            self._pairs(args[1:])
            return
        self._scan(args)

    def _cmd_trades(self, args):
        """last 10 closed; '/trades wins' or '/trades losses' to filter"""
        from src.memory.journal import TradingJournal

        want = args[0].lower() if args else None
        recent = [t for t in TradingJournal().get_recent(50) if t.outcome in ("WIN", "LOSS")]
        if want == "wins":
            rows = sorted([t for t in recent if t.outcome == "WIN"],
                          key=lambda t: t.pnl_pct or 0, reverse=True)[:10]
            title = "🏆 *Top wins*"
        elif want == "losses":
            rows = [t for t in recent if t.outcome == "LOSS" and t.reflection][:10]
            title = "📉 *Losses + lessons*"
            if not rows:
                self._send("No losses logged with lessons yet.")
                return
            self._send(title + "\n" + "\n".join(
                f"{t.symbol} {t.pnl_pct:+.1f}%: {(t.reflection or '')[:120]}" for t in rows))
            return
        else:
            rows = recent[:10]
            title = "*Last trades*"
        if not rows:
            self._send("No closed trades yet.")
            return
        self._send(title + "\n" + "\n".join(
            f"{'✅' if t.outcome == 'WIN' else '❌'} {t.symbol} {t.pnl_pct:+.1f}%" for t in rows))

    def _cmd_learn(self, args):
        """learning status: KB, replay, thresholds, XGBoost; '/learn full' = LESSONS.md"""
        if args and args[0].lower() == "full":
            self._handle_lessons_file(args[1:])
            return
        from src.data.watchlist import ALL_STOCKS
        from src.learning.historical_replay import HistoricalReplayEngine
        from src.memory.adaptive_thresholds import AdaptiveThresholds
        from src.memory.journal import TradingJournal
        from src.tracking.signal_tracker import SignalTracker

        stats = HistoricalReplayEngine().get_stats()
        kb = TradingJournal().get_active_knowledge(min_confidence=0.0)
        top = sorted(kb, key=lambda e: -e.confidence)[:5]
        top_text = "\n".join(f"  [{e.confidence:.2f}] {e.pattern_description[:50]}"
                             for e in top) or "  (building…)"
        from src.memory.adaptive_thresholds import DEFAULT_THRESHOLDS

        thresholds = AdaptiveThresholds().load()
        thresh_lines = []
        for regime, data in sorted(thresholds.items()):
            regime_default = DEFAULT_THRESHOLDS.get(regime, {}).get("judge_min", 6.5)
            diff = data.get("judge_min", regime_default) - regime_default
            arrow = "↑" if diff > 0.1 else "↓" if diff < -0.1 else "→"
            thresh_lines.append(f"  {arrow} {regime}: {data.get('judge_min', regime_default):.1f}/10 "
                                f"({data.get('evidence', 0)}t)")
        signals = SignalTracker().get_best_signals() or "No signal-accuracy data yet."

        try:
            import json as _json

            from src.ml.stock_priors import PRIORS_FILE

            all_priors = {}
            if PRIORS_FILE.exists():
                all_priors = _json.loads(PRIORS_FILE.read_text())
            qualified = {s: d for s, d in all_priors.items() if d.get("total", 0) >= 5}
            best = sorted(qualified.items(), key=lambda x: -x[1]["posterior_win_prob"])[:5]
            worst = sorted(qualified.items(), key=lambda x: x[1]["posterior_win_prob"])[:5]
            best_text = ", ".join(f"{s} ({d['posterior_win_prob']:.0%})" for s, d in best) or "n/a"
            worst_text = ", ".join(f"{s} ({d['posterior_win_prob']:.0%})" for s, d in worst) or "n/a"
        except Exception:
            best_text = worst_text = "n/a"

        try:
            from src.ml.calibration import CalibratedEnsemble

            ensemble_status = CalibratedEnsemble.get_status()
            brier_text = ", ".join(f"{k}={v}" for k, v in ensemble_status["brier_scores"].items()) or "not trained yet"
        except Exception:
            brier_text = "unavailable"

        try:
            regime_stats = HistoricalReplayEngine().get_regime_replay_stats()
            regime_lines = "\n".join(
                f"  {'✅' if v['complete'] else '⏳'} {r}: {v['records']}/{v['target']}"
                for r, v in regime_stats.items())
        except Exception:
            regime_lines = "  Unavailable"

        self._send(
            "📚 *Learning Status*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Patterns: {len(kb)} total | Replay: {stats['total_stocks_replayed']}/{len(ALL_STOCKS)} stocks\n\n"
            f"*Top Patterns:*\n{top_text}\n\n"
            f"*Regime-Stratified Replay:*\n{regime_lines}\n\n"
            f"*Calibrated Ensemble Brier Scores:* {brier_text}\n\n"
            f"*Adaptive Thresholds:*\n" + "\n".join(thresh_lines) + "\n\n"
            f"*Signal accuracy:*\n{signals}\n\n"
            f"*Best-performing stocks:* {best_text}\n"
            f"*Worst-performing stocks:* {worst_text}\n\n"
            "_/learn full sends LESSONS.md._")

    def _cmd_chart(self, args):
        """P&L chart; '/chart wins' for win/loss donut"""
        if args and args[0].lower() == "wins":
            self._win_chart(args[1:])
            return
        self._chart(args)

    def _cmd_trade(self, args):
        """force best-available trade now"""
        self._force_now(args)
        self._forced(args)

    def _cmd_pause(self, args):
        """toggle: pauses if running, resumes if paused"""
        if Path(_PAUSE_FILE).exists():
            self._resume(args)
        else:
            self._pause(args)

    def _brief(self, args):
        """morning market brief"""
        from src.data.market_context import MarketContext

        ctx = MarketContext().get_nifty_context()
        self._send(f"🌅 *Morning brief*\n{ctx.get('context_summary', '')}\n"
                   f"Safe to buy: {'yes' if ctx.get('market_safe_to_buy') else 'no'}")

    def _watchlist(self, args):
        """show watchlist"""
        from src.agents.scout.agent import WATCHLIST

        self._send(f"👁️ Watchlist ({len(WATCHLIST)} stocks):\n"
                   + ", ".join(list(WATCHLIST)[:40]))

    def _alert(self, args):
        """price alerts"""
        self._send("🔔 Price alerts: set via /alert SYMBOL PRICE (coming soon — "
                   "saved to data/cache/price_alerts.json).")

    def _pause(self, args):
        """pause trading"""
        os.makedirs(os.path.dirname(_PAUSE_FILE), exist_ok=True)
        Path(_PAUSE_FILE).touch()
        self._send("⏸️ Trading paused. Jobs will skip until /resume.")

    def _resume(self, args):
        """resume trading"""
        Path(_PAUSE_FILE).unlink(missing_ok=True)
        self._send("▶️ Trading resumed.")

    def _kill(self, args):
        """activate kill switch"""
        Path(LIMITS.kill_switch_file).touch()
        self._send("🚫 KILL SWITCH ACTIVE — all execution halted. /reset_kill to clear.")

    def _reset_kill(self, args):
        """clear kill switch"""
        Path(LIMITS.kill_switch_file).unlink(missing_ok=True)
        self._send("🟢 Kill switch cleared.")

    def _health(self, args):
        """system health"""
        from src.analytics.performance import PerformanceAnalyzer
        from src.memory.journal import TradingJournal
        from src.utils.tips import get_random_tip

        j = TradingJournal()
        summary = PerformanceAnalyzer().get_summary()
        kb = j.get_active_knowledge(min_confidence=0.0)
        try:
            cost = j.get_monthly_cost()
        except Exception:
            cost = 0
        kill = Path(LIMITS.kill_switch_file).exists()
        paused = Path(_PAUSE_FILE).exists()
        status = "🚨 KILL SWITCH" if kill else ("⏸️ PAUSED" if paused else "✅ RUNNING")
        api = "✅ Active" if settings.has_anthropic_key else "🧪 Demo mode"
        groww = "✅ Active" if settings.has_groww_credentials else "⏳ Not yet"
        self._send(
            "🏥 *SYSTEM HEALTH*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Status:    {status}\n"
            f"Mode:      {settings.mode_label}\n"
            f"Anthropic: {api}\n"
            f"Groww:     {groww}\n\n"
            "📊 *TRADING*\n"
            f"Trades:    {summary.get('total_trades', 0)} total\n"
            f"Win rate:  {summary.get('win_rate', 0) * 100:.1f}%\n"
            f"P&L:       ₹{summary.get('total_pnl_inr', 0):+.2f}\n\n"
            "🧠 *LEARNING*\n"
            f"Patterns:  {len(kb)} in knowledge base\n"
            f"API cost:  ₹{cost:.2f} this month\n\n"
            f"💡 _{get_random_tip()}_"
        )

    def _chart(self, args):
        """performance chart"""
        self.tg.send_performance_chart()

    def _win_chart(self, args):
        """win/loss chart"""
        self.tg.send_win_chart()

    def _dashboard_link(self, args):
        """dashboard URL"""
        self.tg.send_dashboard_link()

    def _strategies(self, args):
        """strategy performance"""
        from src.analytics.performance import PerformanceAnalyzer

        bd = PerformanceAnalyzer().get_strategy_breakdown()
        self._send("🧠 *Strategy performance*\n" + "\n".join(
            f"{n}: {('%.0f%%' % (d['win_rate'] * 100)) if d.get('win_rate') is not None else '—'} "
            f"({d['trades']})" for n, d in bd.items()))

    def _learning(self, args):
        """24/7 learning status"""
        from src.data.watchlist import ALL_STOCKS
        from src.learning.historical_replay import HistoricalReplayEngine
        from src.memory.journal import TradingJournal

        stats = HistoricalReplayEngine().get_stats()
        kb = TradingJournal().get_active_knowledge(min_confidence=0.0)
        replay_kb = [e for e in kb if e.category == "HISTORICAL_REPLAY"]
        intraday_kb = [e for e in kb if e.category == "INTRADAY_SIMULATION"]
        real_kb = [e for e in kb if e.category not in ("HISTORICAL_REPLAY", "INTRADAY_SIMULATION")]
        top = sorted(kb, key=lambda e: -e.confidence)[:3]
        top_text = "\n".join(f"  [{e.confidence:.2f}] {e.pattern_description[:50]}"
                             for e in top) or "  (building…)"
        self._send(
            "📚 *Learning Status*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "*Knowledge Base:*\n"
            f"From real trades:    {len(real_kb)} patterns\n"
            f"From intraday sims:  {len(intraday_kb)} patterns\n"
            f"From history replay: {len(replay_kb)} patterns\n"
            f"Total:               {len(kb)} patterns\n\n"
            "*Replay Coverage:*\n"
            f"Stocks replayed: {stats['total_stocks_replayed']}/{len(ALL_STOCKS)}\n"
            f"Stocks pending:  {stats['stocks_pending']}\n\n"
            f"*Top Patterns:*\n{top_text}\n\n"
            "_Replays run every 2h. The system never stops learning._")

    def _forced(self, args):
        """forced learning trade stats"""
        from src.trading.always_on_trader import AlwaysOnTrader

        trader = AlwaysOnTrader()
        s = trader.get_todays_summary()
        history = trader._load_history()
        total_all = len(history)
        wins_all = sum(1 for t in history if t.get("outcome") == "WIN")
        wr_all = round(wins_all / total_all * 100, 1) if total_all else 0
        self._send(
            "📊 *Forced Learning Trades*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "*Today:*\n"
            f"Trades: {s['total']}\n"
            f"Result: {s['wins']}W / {s['losses']}L\n"
            f"Win rate: {s['win_rate']:.0f}%\n\n"
            "*All time:*\n"
            f"Total: {total_all} forced trades\n"
            f"Win rate: {wr_all:.0f}%\n\n"
            "_Paper simulations that run regardless of market conditions.\n"
            "Stop-loss always applied; segregated from real signal metrics._")

    def _force_now(self, args):
        """place forced trades immediately"""
        from src.trading.always_on_trader import AlwaysOnTrader

        trader = AlwaysOnTrader()
        trades = trader.ensure_daily_trades()
        if not trades:
            self._send(f"Already placed {trader._count_todays_forced_trades()} trades today. "
                       f"Daily target ({trader.TARGET_DAILY_TRADES}) reached.")
            return
        for t in trades:
            self._send(
                "⚡ *Forced Trade Placed*\n"
                f"Symbol: *{t['symbol']}*\n"
                f"Entry: ₹{t['entry']:.2f}\n"
                f"Stop: ₹{t['stop']:.2f} _(always applied)_\n"
                f"Target: ₹{t['target']:.2f}\n"
                f"Type: {t['trade_type']}\n"
                f"Score: {t['signal_score']:.2f}\n"
                f"_{t['rationale']}_")

    def _mode(self, args):
        """show current mode + buttons to switch"""
        from src.trading.modes import MODES, get_current_mode, set_mode

        if args and args[0].lower() in MODES:
            cfg = set_mode(args[0].lower())
            self._send(f"{cfg.emoji} Trading mode set to *{cfg.name}*\n{cfg.description}")
            return
        cur = get_current_mode()
        lines = [f"{cur.emoji} *Current mode: {cur.name}*", cur.description, ""]
        for c in MODES.values():
            mark = "→ " if c.name == cur.name else "   "
            lines.append(f"{mark}{c.emoji} {c.name}: judge {c.judge_threshold}/10, "
                         f"{c.max_trades_per_week} trades/wk, size ×{c.position_size_multiplier}")
        self._send_with_mode_buttons("\n".join(lines))

    def _send_with_mode_buttons(self, text: str) -> None:
        keyboard = {"inline_keyboard": [[
            {"text": "🛡️ Conserve", "callback_data": "mode:conserve"},
            {"text": "⚖️ Balanced", "callback_data": "mode:balanced"},
            {"text": "⚡ Rogue", "callback_data": "mode:rogue"},
        ]]}
        try:
            requests.post(
                _API.format(token=settings.telegram_bot_token, method="sendMessage"),
                json={"chat_id": self.tg.chat_id, "text": text[:_MAX], "parse_mode": "Markdown",
                      "reply_markup": keyboard},
                timeout=10)
        except Exception:
            self._send(text)  # fall back to a plain message if the button send fails

    def _handle_callback(self, callback: dict) -> None:
        data = callback.get("data", "")
        cb_id = callback.get("id")
        try:
            requests.post(_API.format(token=settings.telegram_bot_token, method="answerCallbackQuery"),
                         json={"callback_query_id": cb_id}, timeout=10)
        except Exception:
            pass
        if data.startswith("mode:"):
            self._set_mode_cmd(data.split(":", 1)[1])

    def _set_mode_cmd(self, mode: str):
        from src.trading.modes import set_mode

        cfg = set_mode(mode)
        self._send(f"{cfg.emoji} Trading mode set to *{cfg.name}*\n{cfg.description}")

    def _conserve(self, args):
        """protect capital — high bar, half size"""
        self._set_mode_cmd("conserve")

    def _balanced(self, args):
        """default — moderate bar, full size"""
        self._set_mode_cmd("balanced")

    def _rogue(self, args):
        """eager — low bar, trades downtrends"""
        self._set_mode_cmd("rogue")

    def _handle_report(self, args):
        """full dashboard snapshot in one message"""
        import json
        from pathlib import Path

        from src.analytics.performance import PerformanceAnalyzer
        from src.data.regime_detector import RegimeDetector
        from src.data.watchlist import ALL_STOCKS
        from src.learning.historical_replay import HistoricalReplayEngine
        from src.memory.journal import TradingJournal
        from src.trading.always_on_trader import AlwaysOnTrader

        j = TradingJournal()
        pa = PerformanceAnalyzer()
        summary = pa.get_summary()
        regime = RegimeDetector().detect()
        forced = AlwaysOnTrader().get_todays_summary()
        try:
            replay = HistoricalReplayEngine().get_stats()
        except Exception:
            replay = {}
        kb = j.get_active_knowledge(min_confidence=0.0)

        forced_file = Path("data/cache/forced_trades_history.json")
        forced_all = []
        if forced_file.exists():
            try:
                forced_all = json.loads(forced_file.read_text())
            except Exception:
                pass
        forced_wins_all = sum(1 for t in forced_all if t.get("outcome") == "WIN")
        forced_total_all = len(forced_all)
        forced_wr_all = round(forced_wins_all / forced_total_all * 100, 1) if forced_total_all else 0

        try:
            from src.analytics.loss_categorizer import PRE_SNAPSHOT_LABEL
            from src.analytics.loss_categorizer import breakdown as _loss_breakdown

            loss_trades = [(t, t.get("entry_snapshot")) for t in forced_all
                           if t.get("outcome") == "LOSS"]
            raw_breakdown = _loss_breakdown(loss_trades)
            pre_snapshot_count = raw_breakdown.pop(PRE_SNAPSHOT_LABEL, 0)
            # PRE_SNAPSHOT trades have no data to categorize (they predate the
            # entry_snapshot field) — kept out of the categorized list so it
            # doesn't read like "the system doesn't know why N trades lost";
            # reported separately as a plain count instead.
            loss_breakdown = {k: v for k, v in raw_breakdown.items() if v > 0}
            loss_text = ("\n".join(f"  {k}: {v}" for k in sorted(loss_breakdown, key=loss_breakdown.get, reverse=True)
                                   for v in [loss_breakdown[k]])
                        if loss_breakdown else "  No categorized losses yet.")
            if pre_snapshot_count:
                loss_text += f"\n  ({pre_snapshot_count} older trade(s) predate entry_snapshot — uncategorized)"
        except Exception:
            loss_text = "  Unavailable"

        sim_file = Path("data/cache/intraday_sim_history.json")
        sim_all = []
        if sim_file.exists():
            try:
                sim_all = json.loads(sim_file.read_text())
            except Exception:
                pass
        sim_wins = sum(1 for t in sim_all if t.get("outcome") == "WIN")
        sim_total = len(sim_all)
        sim_wr = round(sim_wins / sim_total * 100, 1) if sim_total else 0

        try:
            from src.analytics.era import get_era_start

            era_start_str = (datetime.fromisoformat(get_era_start())
                             .strftime("%d %b %H:%M IST"))
        except Exception:
            era_start_str = "unknown"

        try:
            pro = pa.get_professional_metrics()
        except Exception:
            pro = {"status": "no_data"}
        pro_text = "Not enough data yet"
        if pro.get("status") != "no_data":
            trend_icon = {"IMPROVING": "📈", "STABLE": "➡️", "DECAYING": "📉"}.get(pro.get("trend"), "➡️")
            pf = pro.get("profit_factor", 0)
            pf_str = "∞" if pf >= 999 else f"{pf:.2f}"
            dd_label = pro.get("max_drawdown_label", "Max Drawdown")
            pro_text = (
                f"Profit Factor (real, post-fix era): {pf_str} | "
                f"{dd_label}: {pro.get('max_drawdown_pct', 0):.1f}% "
                f"({pro.get('n_trades', 0)}t post-fix)\n"
                f"Trend: {trend_icon} {pro.get('trend', 'STABLE')} "
                f"(rolling WR {pro.get('rolling_wr_recent20', 0)}% vs prior {pro.get('rolling_wr_prev20', 0)}%)")
            all_time = pro.get("all_time", {})
            if all_time.get("n_trades"):
                at_pf = all_time.get("profit_factor", 0)
                at_pf_str = "∞" if at_pf >= 999 else f"{at_pf:.2f}"
                pro_text += (
                    f"\n_All-time ({pro.get('all_time_label', 'incl. pre-fix era')}, "
                    f"{all_time['n_trades']}t): PF {at_pf_str} | "
                    f"DD {all_time.get('max_drawdown_pct', 0):.1f}%_")

        from src.memory.lessons_writer import _icon_for

        top_kb = sorted(kb, key=lambda e: -e.confidence)[:3]
        kb_lines = []
        for e in top_kb:
            icon = _icon_for(e.pattern_description)
            conf = round(e.confidence * 100)
            desc = e.pattern_description[:55]
            kb_lines.append(f"  {icon} [{conf}%] {desc}")
        kb_text = "\n".join(kb_lines) or "  Still building..."

        regime_emoji_map = {
            "BULL_TRENDING": "🐂", "BEAR_TRENDING": "🐻", "VOLATILE": "⚡",
            "RANGE_BOUND": "↔️", "RECOVERY": "🌱", "TRANSITIONAL": "🌀", "UNKNOWN": "❓",
        }
        regime_name = regime.get("regime", "UNKNOWN")
        regime_emoji = regime_emoji_map.get(regime_name, "❓")
        regime_label = f"{regime_name}{self._stale_suffix(regime)}"
        nifty = regime.get("nifty_price") or 0
        rsi = regime.get("rsi") or 0

        try:
            from src.ml.signal_combiner import get_status as get_xgb_status

            xgb_status = get_xgb_status(j)
        except Exception:
            xgb_status = {"label": "Unavailable", "progress_pct": 0}
        xgb_pct = xgb_status.get("progress_pct", 0)
        xgb_bar = "█" * (xgb_pct // 10) + "░" * (10 - xgb_pct // 10)

        try:
            from src.analytics.strategy_scorecard import EngineScorecard

            scorecard = EngineScorecard().compute()
            throttles = EngineScorecard._load_throttles()
            mode_icon = {"normal": "", "throttled": "🐢 throttled", "paused": "⏸ paused",
                        "probation": "🩹 probation", "retired": "🪦 retired"}
            score_lines = []
            for engine, stats in scorecard.items():
                mode = throttles.get(engine, {}).get("mode", "normal")
                mode_text = mode_icon.get(mode, "")
                pf_str = "∞" if stats["profit_factor"] >= 999 else f"{stats['profit_factor']:.2f}"
                score_lines.append(
                    f"  {engine}: PF {pf_str} · {stats['win_rate'] * 100:.0f}% WR "
                    f"({stats['n_trades']}t) {mode_text}".rstrip())
            scorecard_text = "\n".join(score_lines) or "  Still building..."
        except Exception:
            scorecard_text = "  Unavailable"

        try:
            import json as _json3

            from src.analytics.strategy_scorecard import ADAPTATION_LOG_FILE

            adaptation_history = (_json3.loads(ADAPTATION_LOG_FILE.read_text())
                                  if ADAPTATION_LOG_FILE.exists() else [])
            recent_adaptations = adaptation_history[-3:]
            adapt_lines = [
                f"  [{a['ts'][:16]}] ({a['type']}) {a['detail']}"
                for a in reversed(recent_adaptations)
            ] if recent_adaptations else ["  No self-adjustments yet"]
        except Exception:
            adapt_lines = ["  Unavailable"]

        msg = (
            "📊 *FULL SYSTEM REPORT*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n\n"

            "*🌍 Market*\n"
            f"{regime_emoji} {regime_label}\n"
            f"Nifty ₹{nifty:,.0f} · RSI {rsi:.1f}\n"
            f"Advice: {str(regime.get('strategy', ''))[:60]}\n\n"

            "*📈 Real Pipeline Trades*\n"
            f"Total: {summary.get('total_trades', 0)}\n"
            f"Win rate: {summary.get('win_rate', 0) * 100:.1f}%\n"
            f"P&L: ₹{summary.get('total_pnl_inr', 0):+.2f}\n\n"

            "*📐 Professional Metrics (all sources, net of costs)*\n"
            f"_Era started {era_start_str}_\n"
            f"{pro_text}\n\n"

            "*⚡ Forced Learning (Today)*\n"
            f"Opened today: {forced.get('opened_today', forced['total'])} · "
            f"Closed today: {forced['total']} ({forced['wins']}W/{forced['losses']}L) · "
            f"Currently open: {forced.get('currently_open', 0)}\n"
            f"Win rate (closed): {forced['win_rate']:.0f}%\n\n"

            "*⚡ Forced Learning (All Time)*\n"
            f"{forced_wins_all}W / {forced_total_all - forced_wins_all}L "
            f"from {forced_total_all} trades\n"
            f"Win rate: {forced_wr_all:.0f}%\n\n"

            "*🔬 Intraday Simulations*\n"
            f"{sim_wins}W / {sim_total - sim_wins}L from {sim_total} sims\n"
            f"Win rate: {sim_wr:.0f}%\n\n"

            "*🩺 Loss Reason Breakdown (forced trades, all-time)*\n"
            f"{loss_text}\n\n"

            "*🧠 Knowledge Base*\n"
            f"{len(kb)} patterns learned\n"
            f"Replay coverage: {replay.get('total_stocks_replayed', 0)}/{len(ALL_STOCKS)}\n"
            f"Top patterns:\n{kb_text}\n\n"

            "*🤖 XGBoost Model*\n"
            f"{xgb_bar} {xgb_pct}%\n"
            f"{xgb_status.get('label', '')} (advisory only — never blended into judge score)\n\n"

            "*🔧 Engine Scorecard*\n"
            f"{scorecard_text}\n\n"

            "*🧬 Recent Self-Adjustments*\n"
            f"{chr(10).join(adapt_lines)}\n\n"

            "*💡 What to Improve*\n"
            f"{self._generate_improvement_tip(summary, forced, sim_wr, regime_name)}\n\n"

            f"_Updated: {datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%d %b %H:%M IST')}_"
        )
        self._send(msg)

    def _generate_improvement_tip(self, summary, forced, sim_wr, regime, thresholds=None):
        """Generates honest, specific improvement advice — explains what the
        adaptive learning loop is actually doing, not a generic tip."""
        from src.memory.adaptive_thresholds import DEFAULT_THRESHOLDS, AdaptiveThresholds

        thresh = thresholds if thresholds is not None else AdaptiveThresholds().load()
        regime_data = thresh.get(regime, {})
        regime_default = DEFAULT_THRESHOLDS.get(regime, {}).get("judge_min", 6.5)
        evidence = regime_data.get("evidence", 0)
        regime_wr = regime_data.get("win_rate", None)
        current_threshold = regime_data.get("judge_min", regime_default)

        lines = []

        # A regime with < MIN_EVIDENCE trades of its own says nothing about
        # whether adaptive thresholds are active system-wide — other regimes
        # (e.g. VOLATILE with 500+ trades) can already be adapted while the
        # CURRENT regime just hasn't recurred often enough yet. Showing
        # "0/15" here used to read as "the system failed to learn" instead of
        # "this specific regime is rare" — so say nothing rather than mislead.
        if regime_wr is not None:
            wr_pct = regime_wr * 100
            direction = "lowered (easier)" if current_threshold < regime_default else "raised (harder)"
            lines.append(
                f"*Adaptive threshold ({regime}):* {current_threshold:.1f}/10 — "
                f"{direction} based on {evidence} trades ({wr_pct:.0f}% win rate).")

        if summary.get("total_trades", 0) == 0:
            lines.append(
                "*Real pipeline trades:* 0 so far. This is normal — the pipeline "
                "correctly protects from bad companies (Piotroski F=0) and choppy "
                "markets (ADX<15). Run /scan at 10AM IST for best results.")

        return "\n".join(lines) if lines else "System is performing as expected."

    def _handle_totals(self, args):
        """trade totals across all sources"""
        import json
        from pathlib import Path

        from src.memory.journal import TradingJournal

        merged = []
        for fname, source in (
            ("data/cache/forced_trades_history.json", "Forced"),
            ("data/cache/intraday_sim_history.json", "Intraday"),
            ("data/cache/short_trades_history.json", "Short"),
        ):
            p = Path(fname)
            if p.exists():
                try:
                    for t in json.loads(p.read_text()):
                        if t.get("outcome") in ("WIN", "LOSS"):
                            merged.append({
                                "source": source, "outcome": t["outcome"],
                                "symbol": t.get("symbol", "?"), "pnl": t.get("pnl_pct", 0) or 0,
                            })
                except Exception:
                    pass

        j = TradingJournal()
        for t in j.get_recent(n=200):
            if t.outcome in ("WIN", "LOSS"):
                merged.append({
                    "source": "Real", "outcome": t.outcome,
                    "symbol": t.symbol, "pnl": t.pnl_pct or 0,
                })

        total = len(merged)
        won = sum(1 for t in merged if t["outcome"] == "WIN")
        lost = total - won
        wr = round(won / total * 100, 1) if total else 0

        by_source = {}
        for t in merged:
            s = t["source"]
            by_source.setdefault(s, {"total": 0, "won": 0, "lost": 0, "pnl": 0})
            by_source[s]["total"] += 1
            if t["outcome"] == "WIN":
                by_source[s]["won"] += 1
            else:
                by_source[s]["lost"] += 1
            by_source[s]["pnl"] += t["pnl"]

        bar_won = "█" * round(wr / 10) + "░" * (10 - round(wr / 10))

        lines = [
            "📊 *TRADE TOTALS — ALL SOURCES*",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"Total taken: *{total}*",
            f"Won: *{won}* ✅   Lost: *{lost}* ❌",
            f"Win rate: *{wr}%*  {bar_won}",
            "",
            "*By source:*",
        ]
        for src, stats in sorted(by_source.items()):
            src_wr = round(stats["won"] / stats["total"] * 100) if stats["total"] else 0
            avg_pnl = stats["pnl"] / stats["total"] if stats["total"] else 0
            lines.append(
                f"{src}: {stats['total']} trades ({stats['won']}W/{stats['lost']}L) "
                f"{src_wr}% WR  avg {avg_pnl:+.1f}%"
            )

        avg_win = sum(t["pnl"] for t in merged if t["pnl"] > 0) / max(won, 1)
        avg_loss = sum(t["pnl"] for t in merged if t["pnl"] < 0) / max(lost, 1)
        expectancy = wr / 100 * avg_win + (1 - wr / 100) * avg_loss
        breakeven_wr = (abs(avg_loss) / (avg_win + abs(avg_loss)) * 100
                        if (avg_win + abs(avg_loss)) > 0 else 50)

        lines.append("")
        lines.append("*Risk-Adjusted View:*")
        lines.append(f"Avg win: {avg_win:+.1f}%  |  Avg loss: {avg_loss:+.1f}%")
        lines.append(f"Expectancy: {expectancy:+.2f}% per trade")
        lines.append(f"Break-even WR needed: {breakeven_wr:.0f}%")
        if expectancy > 0:
            lines.append("✅ *System is EV-positive despite low WR*")
        else:
            lines.append("⚠️ *Negative EV — R:R needs improvement*")

        self._send("\n".join(lines))

    def _handle_thresholds(self, args):
        """adaptive judge thresholds by regime"""
        from src.memory.adaptive_thresholds import DEFAULT_THRESHOLDS, AdaptiveThresholds

        thresholds = AdaptiveThresholds().load()
        lines = ["📊 *Adaptive Thresholds*", "_(System self-adjusts based on trade outcomes)_\n"]
        for regime, data in sorted(thresholds.items()):
            evidence = data.get("evidence", 0)
            wr = data.get("win_rate", None)
            default = DEFAULT_THRESHOLDS.get(regime, {}).get("judge_min", 6.5)
            threshold = data.get("judge_min", default)
            diff = threshold - default
            arrow = "↑" if diff > 0.1 else "↓" if diff < -0.1 else "→"
            wr_str = f" WR={wr:.0%}" if wr else " (no data yet)"
            lines.append(f"{arrow} {regime}: {threshold:.1f}/10 ({evidence} trades{wr_str})")
        self._send("\n".join(lines))

    def _handle_lessons_file(self, args):
        """sends the current LESSONS.md"""
        lf = Path("LESSONS.md")
        if not lf.exists():
            from src.memory.lessons_writer import LessonsWriter

            LessonsWriter().write()
        content = lf.read_text()[:3000]
        self._send(f"```\n{content}\n```")

    def _handle_flows(self, args):
        """institutional flows + economic calendar"""
        from src.data.realtime_feeds import EconomicCalendar, FIIDIIFeed

        fiidii = FIIDIIFeed().get_latest()
        upcoming = EconomicCalendar().get_upcoming(days_ahead=14)
        lines = ["💹 *Institutional Flows + Calendar*", "━━━━━━━━━━━━━━━━━━━━━━━━"]
        if fiidii.get("unavailable"):
            lines.append("*FII/DII:* unavailable — no successful fetch yet")
        else:
            fii = fiidii.get("fii_net_crore", 0)
            dii = fiidii.get("dii_net_crore", 0)
            signal = fiidii.get("signal", "NEUTRAL")
            emoji = "🟢" if signal == "BULLISH" else "🔴" if signal == "BEARISH" else "🟡"
            as_of = f" (as of {fiidii['as_of']})" if fiidii.get("stale") and fiidii.get("as_of") else ""
            lines.append(f"*FII:* {fii:+,.0f} Cr {emoji}{as_of}")
            lines.append(f"*DII:* {dii:+,.0f} Cr{as_of}")
            lines.append(f"*Signal:* {fiidii.get('signal_reason', '')}")
        lines += ["", "*Upcoming Events:*"]
        for ev in upcoming[:5]:
            impact_emoji = "🚨" if ev["impact"] == "HIGH" else "⚠️"
            lines.append(f"{impact_emoji} *{ev['event']}* — {ev['days_away']}d away")
        if not upcoming:
            lines.append("No major events in next 14 days ✅")
        self._send("\n".join(lines))

    def _handle_diagnose(self, args):
        """runs diagnose.py — why real trades aren't clearing the pipeline"""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "scripts/diagnose.py"],
            capture_output=True, text=True, timeout=60, cwd=".")
        output = result.stdout[-3000:] if result.stdout else (result.stderr or "")[-3000:]
        self._send(f"```\n{output}\n```")

    def _handle_stock(self, args):
        """dossier summary for /stock SYMBOL: sector, fundamentals, trade
        record, best/worst regime, Bayesian win prob, recent notes"""
        if not args:
            self._send("Usage: /stock SYMBOL")
            return
        symbol = args[0].upper()
        from src.memory.company_dossier import dossier_store

        dossier = dossier_store.get(symbol)
        agg = dossier.get("aggregates", {})
        fh = dossier.get("fundamentals_history") or []
        latest_fund = fh[-1] if fh else {}
        notes = dossier.get("notes") or []
        lines = [
            f"📁 *{symbol} Dossier*",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"Sector: {dossier.get('sector') or 'unknown'} | Tier: {dossier.get('tier') or 'unknown'}",
            "",
            "*Latest fundamentals:*",
            f"ROCE {latest_fund.get('roce_pct')}% | ROE {latest_fund.get('roe_pct')}% | "
            f"D/E {latest_fund.get('de_ratio')} | Piotroski {latest_fund.get('piotroski_score')}/9"
            if latest_fund else "No fundamentals recorded yet.",
            "",
            "*Our trade record:*",
            f"{agg.get('wins', 0)}W / {agg.get('losses', 0)}L "
            f"({agg.get('win_rate', 0):.0f}% WR, {agg.get('total_trades', 0)} trades)",
            f"Avg P&L: {agg.get('avg_pnl_pct', 0):+.2f}% | Avg hold: {agg.get('avg_hold_days', 0):.1f}d",
            f"Best regime: {agg.get('best_regime') or '—'} | Worst regime: {agg.get('worst_regime') or '—'}",
            f"Bayesian win prob: {agg.get('bayesian_win_prob', 0.5):.0%}",
        ]
        if notes:
            lines += ["", "*Recent notes:*"] + [f"  {n}" for n in notes[-5:]]
        self._send("\n".join(lines))

    def _handle_jobs(self, args):
        """per-job last run, avg duration, failure rate over last 20 runs"""
        from src.analytics.job_telemetry import get_job_stats

        stats = get_job_stats()
        if not stats:
            self._send("📋 No job telemetry recorded yet.")
            return
        lines = ["📋 *Job Telemetry (last 20 runs each)*", "━━━━━━━━━━━━━━━━━━━━━━━━"]
        for job_name, s in sorted(stats.items()):
            icon = "✅" if s["last_ok"] else "❌"
            warn = " ⚠️" if s["failure_rate"] > 0.3 or s["avg_duration_s"] > 120 else ""
            lines.append(
                f"{icon} {job_name}: avg {s['avg_duration_s']:.1f}s, "
                f"fail rate {s['failure_rate']:.0%} ({s['n_runs']}r){warn}")
        self._send("\n".join(lines))

    def _handle_health_check(self, args):
        """runs the full 9-component system health check on demand"""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "scripts/system_health_check.py"],
            capture_output=True, text=True, timeout=120, cwd=".")
        output = (result.stdout or result.stderr or "No output")[-3000:]
        self._send(f"```\n{output[:2000]}\n```")

    def _pairs(self, args):
        """pairs opportunities"""
        from src.strategies.pairs_trading import PairsTradingStrategy

        self._send("🔗 Scanning pairs…")
        opps = PairsTradingStrategy().find_opportunities()
        if not opps:
            self._send("No pair divergences beyond 2σ today.")
            return
        self._send("🔗 *Pairs scan*\n" + "\n".join(
            f"Buy {o['buy_symbol']} vs {o['pair_symbol']} z={o['z_score']}" for o in opps[:5]))
