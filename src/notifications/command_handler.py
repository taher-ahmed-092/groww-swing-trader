"""
Telegram command handler — a background polling thread that answers /commands.

Sync HTTP (same pattern as TelegramNotifier). Every handler is defensive: a failing
command logs and is skipped, never crashing the loop. Started from runner.py.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

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
            "/pairs": self._pairs,
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
