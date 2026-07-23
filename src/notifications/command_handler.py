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
    def __init__(self) -> None:
        self.tg = TelegramNotifier()
        self.commands = {
            "/start": self._start, "/help": self._help, "/status": self._status,
            "/positions": self._positions, "/portfolio": self._portfolio,
            "/funds": self._funds, "/performance": self._performance,
            "/trades": self._trades, "/wins": self._wins, "/lessons": self._lessons,
            "/knowledge": self._knowledge, "/signals": self._signals,
            "/evaluation": self._evaluation, "/enhance": self._enhance,
            "/params": self._params, "/regime": self._regime, "/scan": self._scan,
            "/brief": self._brief, "/watchlist": self._watchlist, "/alert": self._alert,
            "/pause": self._pause, "/resume": self._resume, "/kill": self._kill,
            "/reset_kill": self._reset_kill, "/health": self._health,
            "/chart": self._chart, "/win_chart": self._win_chart,
            "/dashboard_link": self._dashboard_link, "/strategies": self._strategies,
            "/pairs": self._pairs, "/mode": self._mode, "/conserve": self._conserve,
            "/balanced": self._balanced, "/rogue": self._rogue,
            "/learning": self._learning, "/forced": self._forced,
            "/force_now": self._force_now, "/report": self._handle_report,
            "/lessons_file": self._handle_lessons_file,
            "/thresholds": self._handle_thresholds, "/totals": self._handle_totals,
        }

    # ── lifecycle ──────────────────────────────────────────────────────────────
    def setup(self) -> None:
        """Register the / menu in Telegram."""
        if not self.tg.is_configured():
            return
        cmds = [{"command": c.lstrip("/"), "description": h.__doc__ or c.lstrip("/")}
                for c, h in self.commands.items()]
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
            "📊 *TRADING*\n"
            "/scan — scan the watchlist now\n"
            "/pairs — market-neutral scan (any market)\n"
            "/positions — open positions\n"
            "/brief — today's market brief\n\n"
            "📈 *PERFORMANCE*\n"
            "/performance — win rate + stats\n"
            "/chart — P&L chart image\n"
            "/trades — last 10 trades\n"
            "/wins — greatest wins\n"
            "/lessons — what losses taught us\n\n"
            "🧠 *LEARNING*\n"
            "/knowledge — what the system learned\n"
            "/regime — current market regime\n"
            "/evaluation — agent accuracy\n"
            "/enhance — weekly improvement ideas\n\n"
            "🖥️ *SYSTEM*\n"
            "/dashboard_link — your private dashboard\n"
            "/health — full system status\n"
            "/kill — emergency stop\n"
            "/help — detailed command list\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 _{get_random_tip()}_"
        )

    def _help(self, args):
        """grouped command list"""
        self._send(
            "*Commands*\n"
            "Trading: /status /positions /scan /pause /resume /kill /reset_kill\n"
            "Portfolio: /portfolio /funds /performance /trades /wins /lessons\n"
            "Learning: /knowledge /signals /evaluation /enhance /params /regime /strategies\n"
            "Markets: /brief /pairs /watchlist /alert\n"
            "Charts: /chart /win_chart /dashboard_link\n"
            "System: /health")

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
        self._send(
            f"{emoji} *MARKET REGIME: {regime}*\n"
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
        """show/set trading mode"""
        from src.trading.modes import MODES, get_current_mode, set_mode

        if args and args[0].lower() in MODES:
            cfg = set_mode(args[0].lower())
            self._send(f"{cfg.emoji} Trading mode set to *{cfg.name}*\n{cfg.description}")
            return
        cur = get_current_mode()
        lines = [f"{cur.emoji} *Current mode: {cur.name}*", cur.description, "",
                 "Switch with /conserve, /balanced, /rogue:"]
        for c in MODES.values():
            mark = "→ " if c.name == cur.name else "   "
            lines.append(f"{mark}{c.emoji} {c.name}: judge {c.judge_threshold}/10, "
                         f"{c.max_trades_per_week} trades/wk, size ×{c.position_size_multiplier}")
        self._send("\n".join(lines))

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
        from src.learning.historical_replay import HistoricalReplayEngine
        from src.memory.journal import TradingJournal
        from src.trading.always_on_trader import AlwaysOnTrader
        from src.data.watchlist import ALL_STOCKS

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

        top_kb = sorted(kb, key=lambda e: -e.confidence)[:3]
        kb_lines = []
        for e in top_kb:
            icon = "✅" if "WON" in e.pattern_description else "❌"
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
        nifty = regime.get("nifty_price") or 0
        rsi = regime.get("rsi") or 0

        total_learning = forced_total_all + sim_total
        xgb_pct = min(100, round(total_learning / 30 * 100))
        xgb_bar = "█" * (xgb_pct // 10) + "░" * (10 - xgb_pct // 10)

        msg = (
            "📊 *FULL SYSTEM REPORT*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n\n"

            "*🌍 Market*\n"
            f"{regime_emoji} {regime_name}\n"
            f"Nifty ₹{nifty:,.0f} · RSI {rsi:.1f}\n"
            f"Advice: {str(regime.get('strategy', ''))[:60]}\n\n"

            "*📈 Real Pipeline Trades*\n"
            f"Total: {summary.get('total_trades', 0)}\n"
            f"Win rate: {summary.get('win_rate', 0) * 100:.1f}%\n"
            f"P&L: ₹{summary.get('total_pnl_inr', 0):+.2f}\n\n"

            "*⚡ Forced Learning (Today)*\n"
            f"{forced['wins']}W / {forced['losses']}L from {forced['total']} trades\n"
            f"Win rate: {forced['win_rate']:.0f}%\n\n"

            "*⚡ Forced Learning (All Time)*\n"
            f"{forced_wins_all}W / {forced_total_all - forced_wins_all}L "
            f"from {forced_total_all} trades\n"
            f"Win rate: {forced_wr_all:.0f}%\n\n"

            "*🔬 Intraday Simulations*\n"
            f"{sim_wins}W / {sim_total - sim_wins}L from {sim_total} sims\n"
            f"Win rate: {sim_wr:.0f}%\n\n"

            "*🧠 Knowledge Base*\n"
            f"{len(kb)} patterns learned\n"
            f"Replay coverage: {replay.get('total_stocks_replayed', 0)}/{len(ALL_STOCKS)}\n"
            f"Top patterns:\n{kb_text}\n\n"

            "*🤖 XGBoost Model*\n"
            f"{xgb_bar} {xgb_pct}%\n"
            f"Needs {max(0, 30 - total_learning)} more trades\n\n"

            "*💡 What to Improve*\n"
            f"{self._generate_improvement_tip(summary, forced, sim_wr, regime_name)}\n\n"

            f"_Updated: {datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%d %b %H:%M IST')}_"
        )
        self._send(msg)

    def _generate_improvement_tip(self, summary, forced, sim_wr, regime, thresholds=None):
        """Generates honest, specific improvement advice — explains what the
        adaptive learning loop is actually doing, not a generic tip."""
        from src.memory.adaptive_thresholds import AdaptiveThresholds

        thresh = thresholds if thresholds is not None else AdaptiveThresholds().load()
        regime_data = thresh.get(regime, {})
        evidence = regime_data.get("evidence", 0)
        regime_wr = regime_data.get("win_rate", None)
        current_threshold = regime_data.get("judge_min", 6.5)

        lines = []
        forced_total = forced.get("total", 0)

        if forced_total < 15:
            lines.append(
                f"*Learning phase:* {forced_total}/15 trades needed before adaptive "
                "thresholds activate. System is accumulating evidence.")
        elif regime_wr is not None:
            wr_pct = regime_wr * 100
            direction = "lowered (easier)" if current_threshold < 6.5 else "raised (harder)"
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

        self._send("\n".join(lines))

    def _handle_thresholds(self, args):
        """adaptive judge thresholds by regime"""
        from src.memory.adaptive_thresholds import AdaptiveThresholds

        thresholds = AdaptiveThresholds().load()
        lines = ["📊 *Adaptive Thresholds*", "_(System self-adjusts based on trade outcomes)_\n"]
        for regime, data in sorted(thresholds.items()):
            evidence = data.get("evidence", 0)
            wr = data.get("win_rate", None)
            threshold = data.get("judge_min", 6.5)
            default = 6.5
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
