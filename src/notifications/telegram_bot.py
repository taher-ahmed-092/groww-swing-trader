"""
Telegram notifier — alerts, trade cards with inline keyboards, and remote approval.

All SENDS use synchronous HTTP (requests) so they work from sync LangGraph nodes
with no event-loop conflicts. Incoming /commands are handled separately by
command_handler.CommandHandler (a background polling thread). Fully optional: with
no token configured every method degrades gracefully and never raises.
"""
from __future__ import annotations

import random
import time
from pathlib import Path

import requests
from rich.console import Console

from config.settings import settings
from src.orchestrator.state import TradeState

console = Console()

_API_BASE = "https://api.telegram.org/bot{token}/{method}"
_POLL_INTERVAL_SECONDS = 5
_EMOJIS = ["📊", "📈", "🎯", "💹", "🔔", "⚡", "🌟"]


class TelegramNotifier:
    def __init__(self) -> None:
        self.token = settings.telegram_bot_token
        # Telegram chat ids are integers; normalise to the canonical str form so
        # every send uses a valid chat_id (a non-numeric .env value disables sends).
        chat_int = settings.telegram_chat_id_int
        self.chat_id = str(chat_int) if chat_int is not None else ""
        self.enabled = bool(self.token and chat_int is not None)

    def is_configured(self) -> bool:
        return self.enabled

    def test_connection(self) -> dict:
        """Verify the bot token via getMe and send a hello message. Called on startup."""
        if not self.is_configured():
            return {"ok": False, "reason": "Missing BOT_TOKEN or CHAT_ID in .env"}
        try:
            r = requests.get(_API_BASE.format(token=self.token, method="getMe"), timeout=10)
            if r.status_code != 200:
                return {"ok": False, "reason": f"Invalid token (HTTP {r.status_code})"}
            bot_name = r.json()["result"]["first_name"]
            self.send_message(
                f"🚀 *{bot_name}* is online!\n\n"
                f"Mode: {settings.mode_label}\n"
                "Type /start to see all commands.\n\n"
                "_The system is watching the markets for you._"
            )
            return {"ok": True, "bot_name": bot_name}
        except (requests.RequestException, ValueError, KeyError) as exc:
            return {"ok": False, "reason": str(exc)}

    # ── low-level ──────────────────────────────────────────────────────────────
    def _call(self, method: str, params: dict, timeout: int = 15) -> dict | None:
        if not self.token:
            return None
        try:
            resp = requests.post(_API_BASE.format(token=self.token, method=method),
                                 json=params, timeout=timeout)
            data = resp.json()
            if not data.get("ok"):
                console.print(f"[yellow][TELEGRAM] {method} not ok: {data.get('description')}[/yellow]")
                return None
            return data
        except (requests.RequestException, ValueError) as exc:
            console.print(f"[yellow][TELEGRAM] {method} failed: {exc}[/yellow]")
            return None

    # ── sends ────────────────────────────────────────────────────────────────
    def send_message(self, text: str) -> bool:
        if not self.enabled:
            return False
        result = self._call("sendMessage", {"chat_id": self.chat_id, "text": text,
                                            "parse_mode": "Markdown"})
        if result is None:
            # Retry once without Markdown (a stray * / _ can make Telegram 400).
            result = self._call("sendMessage", {"chat_id": self.chat_id, "text": text})
        return result is not None

    def send_photo(self, image_bytes: bytes, caption: str = "") -> bool:
        if not self.enabled or not image_bytes:
            return False
        try:
            r = requests.post(
                _API_BASE.format(token=self.token, method="sendPhoto"),
                data={"chat_id": self.chat_id, "caption": caption},
                files={"photo": ("chart.png", image_bytes, "image/png")},
                timeout=30,
            )
            return r.status_code == 200
        except requests.RequestException as exc:
            console.print(f"[yellow][TELEGRAM] sendPhoto failed: {exc}[/yellow]")
            return False

    def send_performance_chart(self) -> None:
        from dashboard.chart_generator import generate_performance_chart
        from src.memory.journal import TradingJournal

        recent = [{"symbol": t.symbol, "pnl_pct": t.pnl_pct or 0}
                  for t in TradingJournal().get_recent(n=20) if t.outcome in ("WIN", "LOSS")]
        self.send_photo(generate_performance_chart(recent), caption="📊 Your Performance Chart")

    def send_win_chart(self) -> None:
        from dashboard.chart_generator import generate_win_rate_chart
        from src.analytics.performance import PerformanceAnalyzer

        s = PerformanceAnalyzer().get_summary()
        summary = {"total_trades": s.get("total_trades", 0),
                   "win_rate": round(s.get("win_rate", 0) * 100, 1),
                   "avg_win_pct": s.get("avg_win_pct", 0), "avg_loss_pct": s.get("avg_loss_pct", 0)}
        self.send_photo(generate_win_rate_chart(summary), caption="🥧 Win/Loss Breakdown")

    def send_dashboard_link(self) -> None:
        if not settings.has_dashboard_token:
            self.send_message("Dashboard not configured. Add DASHBOARD_SECRET_TOKEN to .env")
            return
        url = (f"http://localhost:{settings.dashboard_port}/dashboard/"
               f"{settings.dashboard_secret_token}")
        self.send_message(
            f"🖥️ Your Private Dashboard\n\nLocal: {url}\n\n"
            "For phone access, run: bash deploy/setup_cloudflare_tunnel.sh"
        )

    # ── trade card ─────────────────────────────────────────────────────────────
    def _build_trade_card_text(self, state: dict) -> str:
        tech = state.get("technical_verdict", {}) or {}
        fund = state.get("fundamental_verdict", {}) or {}
        judge = state.get("judge_verdict", {}) or {}
        gut = state.get("gut_check", {}) or {}
        sizing = state.get("risk_check", {}) or {}
        tw = tech.get("time_window", {}) or {}

        entry = tech.get("entry_price", 0) or 0
        stop = tech.get("stop_price", 0) or 0
        target = tech.get("target_price", 0) or 0
        rr = ((target - entry) / (entry - stop)) if (entry - stop) > 0 else 0
        score = judge.get("overall_score", 0) or 0
        bar = "█" * round(score) + "░" * (10 - round(score))

        try:
            from src.utils.summarizer import TradeSummarizer
            summary = TradeSummarizer().rule_based_summary(state)
        except Exception:
            summary = ""
        try:
            from src.utils.tips import get_contextual_tip
            tip = get_contextual_tip(tech.get("signal"),
                                     state.get("market_context", {}).get("nifty_trend"),
                                     judge.get("flags", []))
        except Exception:
            tip = ""

        def pct(n):
            return (n / entry * 100) if entry else 0

        f5 = round((fund.get("score", 0) or 0) * 5)
        t5 = round((tech.get("score", 0) or 0) * 5)
        lines = [
            f"{random.choice(_EMOJIS)} *TRADE PROPOSAL*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📌 *{state.get('symbol', '')}*  {state.get('sector', '')}  "
            f"{str(state.get('market_context', {}).get('regime', ''))[:10]}",
            f"{settings.mode_label}",
            "",
            "🧠 *SUMMARY*", f"_{summary}_", "",
            f"📊 *CONFIDENCE*  {bar}  *{score:.1f}/10*", "",
            f"💰 Entry: ₹{entry:,.2f}",
            f"🛑 Stop:  ₹{stop:,.2f}  _(-{pct(entry - stop):.1f}%)_",
            f"🎯 Target: ₹{target:,.2f}  _(+{pct(target - entry):.1f}%)_",
            f"📐 R:R 1:{rr:.1f}  💸 ₹{sizing.get('position_size_inr', 0):.0f}  "
            f"_{sizing.get('confidence_tier', '')}_",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📈 Fund  {'▓' * f5}{'░' * (5 - f5)}  {fund.get('score', 0) or 0:.2f}",
            f"🔧 Tech  {'▓' * t5}{'░' * (5 - t5)}  {tech.get('score', 0) or 0:.2f}  [{tech.get('signal', '')}]",
            f"🧑‍⚖️ Judge  {score:.1f}/10",
            f"🤔 Gut  {gut.get('gut_score', 0):.1f}/10  —  {gut.get('gut_verdict', '')}",
        ]
        flags = [f for f in judge.get("flags", []) if f != "DEMO_MODE"]
        lines.append(f"⚠️ Flags: {', '.join(flags[:3])}" if flags else "✅ No flags")
        if state.get("manually_requested"):
            lines.append("🔴 *MANUALLY REQUESTED — emotional check*")
        if tw.get("countdown_display"):
            lines += ["", tw["countdown_display"]]
        lines += ["", f"💡 _{tip}_",
                  f"⏰ _Auto-rejects in {settings.auto_approve_timeout_seconds // 60}min_"]
        return "\n".join(lines)

    def send_trade_card_with_keyboard(self, state: dict) -> str | None:
        if not self.enabled:
            return None
        text = self._build_trade_card_text(state)
        symbol = state.get("symbol", "")
        size = (state.get("risk_check", {}) or {}).get("position_size_inr", 500) or 500
        keyboard = {"inline_keyboard": [
            [{"text": f"✅ Approve ₹{size:.0f}", "callback_data": f"approve:{symbol}"},
             {"text": "❌ Reject", "callback_data": f"reject:{symbol}"}],
            [{"text": "🔍 Details", "callback_data": f"details:{symbol}"},
             {"text": "⏰ Snooze 30m", "callback_data": f"snooze:{symbol}"}],
            [{"text": "🚫 Kill Switch", "callback_data": "kill"},
             {"text": "📊 Positions", "callback_data": "positions"}],
        ]}
        result = self._call("sendMessage", {
            "chat_id": self.chat_id, "text": text, "parse_mode": "Markdown",
            "reply_markup": keyboard,
        })
        if result is None:  # Markdown fallback
            result = self._call("sendMessage", {
                "chat_id": self.chat_id, "text": text, "reply_markup": keyboard})
        return str(result["result"]["message_id"]) if result else None

    # Backward-compatible name used by the executor node.
    def send_trade_card(self, state: TradeState) -> str | None:
        return self.send_trade_card_with_keyboard(dict(state))

    # ── approval (sync — safe from LangGraph nodes) ─────────────────────────────
    def wait_for_approval(self, timeout_seconds: int = 1800, symbol: str = "") -> bool:
        if not self.enabled:
            if settings.auto_approve_if_no_telegram:
                console.print("[yellow][TELEGRAM] No bot — auto-approving (paper convenience).[/yellow]")
                return True
            try:
                return input("Approve trade? [y/N]: ").strip().lower() in ("y", "yes")
            except EOFError:
                return False

        baseline = self._call("getUpdates", {"timeout": 0}, timeout=20)
        offset = 0
        if baseline and baseline.get("result"):
            offset = baseline["result"][-1]["update_id"] + 1

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            data = self._call("getUpdates", {"offset": offset, "timeout": 10}, timeout=20)
            for update in (data or {}).get("result", []):
                offset = update["update_id"] + 1
                cb = (update.get("callback_query") or {}).get("data", "")
                msg = (update.get("message") or {}).get("text", "").strip().lower()
                if cb == "kill":
                    Path(settings_kill_file()).touch()
                    self.send_message("🚫 Kill switch activated.")
                    return False
                if cb == f"approve:{symbol}" or (not symbol and cb.startswith("approve")) or msg.startswith("/approve"):
                    self.send_message("✅ Trade approved — placing order.")
                    return True
                if cb == f"reject:{symbol}" or (not symbol and cb.startswith("reject")) or msg.startswith("/reject"):
                    self.send_message("❌ Trade rejected.")
                    return False
            time.sleep(_POLL_INTERVAL_SECONDS)

        self.send_message("⏳ No reply within window — auto-rejecting (safe default).")
        return False


def settings_kill_file() -> str:
    from config.risk_limits import LIMITS
    return LIMITS.kill_switch_file
