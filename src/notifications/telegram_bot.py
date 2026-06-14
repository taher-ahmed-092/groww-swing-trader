"""
Telegram notifier — remote trade approval and alerts from your phone.

Implemented over the Telegram Bot HTTP API with `requests` (synchronous), which
fits the polling approval model cleanly. Fully optional: if TELEGRAM_BOT_TOKEN /
TELEGRAM_CHAT_ID are not set, every method degrades gracefully and never raises.
"""
from __future__ import annotations

import time

import requests
from rich.console import Console

from config.settings import settings
from src.orchestrator.state import TradeState

console = Console()

_API_BASE = "https://api.telegram.org/bot{token}/{method}"
_POLL_INTERVAL_SECONDS = 5


class TelegramNotifier:
    def __init__(self) -> None:
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.enabled = bool(self.token and self.chat_id)
        if not self.enabled:
            console.print(
                "[yellow][TELEGRAM] Disabled (no TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).[/yellow]"
            )

    # ── low-level ──────────────────────────────────────────────────────────────
    def _call(self, method: str, params: dict, timeout: int = 15) -> dict | None:
        if not self.token:
            return None
        try:
            resp = requests.post(
                _API_BASE.format(token=self.token, method=method),
                json=params,
                timeout=timeout,
            )
            data = resp.json()
            if not data.get("ok"):
                console.print(f"[yellow][TELEGRAM] {method} not ok: {data.get('description')}[/yellow]")
                return None
            return data
        except (requests.RequestException, ValueError) as exc:
            console.print(f"[yellow][TELEGRAM] {method} failed: {exc}[/yellow]")
            return None

    # ── public API ─────────────────────────────────────────────────────────────
    def send_message(self, text: str) -> None:
        if not self.enabled:
            return
        self._call("sendMessage", {"chat_id": self.chat_id, "text": text})

    def send_photo(self, png_bytes: bytes, caption: str = "") -> None:
        """Upload a PNG (e.g. a matplotlib chart) via the Telegram HTTP API."""
        if not self.enabled or not png_bytes:
            return
        try:
            requests.post(
                _API_BASE.format(token=self.token, method="sendPhoto"),
                data={"chat_id": self.chat_id, "caption": caption},
                files={"photo": ("chart.png", png_bytes, "image/png")},
                timeout=30,
            )
        except requests.RequestException as exc:
            console.print(f"[yellow][TELEGRAM] sendPhoto failed: {exc}[/yellow]")

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

    def send_trade_card(self, state: TradeState) -> str | None:
        if not self.enabled:
            console.print("[yellow][TELEGRAM] send_trade_card skipped — notifier disabled.[/yellow]")
            return None

        fundamental = state.get("fundamental_verdict", {})
        technical = state.get("technical_verdict", {})
        judge = state.get("judge_verdict", {})

        entry = technical.get("entry_price") or 0
        stop = technical.get("stop_price") or 0
        target = technical.get("target_price") or 0
        rr = round((target - entry) / (entry - stop), 2) if (entry - stop) else "n/a"

        mode = "LIVE" if settings.broker_mode == "live" else "PAPER"
        approved = "✅ APPROVED" if judge.get("approved") else "❌ NOT APPROVED"
        flags = judge.get("flags") or []
        flags_str = ", ".join(flags) if flags else "None"

        lines = [
            f"📊 TRADE PROPOSAL — {state.get('symbol')}",
            f"Mode: {mode}",
            "─────────────────",
            f"Fundamental: {fundamental.get('score')}/1.0 — {fundamental.get('moat', 'n/a')}",
            (
                f"Technical: {technical.get('signal')} {technical.get('score')}/1.0 — "
                f"Entry ₹{entry} | Stop ₹{stop} | Target ₹{target}"
            ),
            f"R:R Ratio: {rr}",
            f"Judge: {approved} | Confidence: {judge.get('score') or judge.get('overall_score')}",
            f"Flags: {flags_str}",
            "─────────────────",
        ]
        if state.get("manually_requested"):
            lines.append("⚠️ MANUALLY REQUESTED (anti-emotional flag)")
            lines.append("─────────────────")
        lines.append("Reply /approve or /reject within 30 min. No reply = auto-reject.")

        result = self._call("sendMessage", {"chat_id": self.chat_id, "text": "\n".join(lines)})
        if result:
            return str(result.get("result", {}).get("message_id"))
        return None

    def wait_for_approval(self, timeout_seconds: int = 1800) -> bool:
        # No bot configured: paper convenience or CLI fallback.
        if not self.enabled:
            if settings.auto_approve_if_no_telegram:
                console.print("[yellow][TELEGRAM] No bot — auto-approving (paper convenience).[/yellow]")
                return True
            try:
                answer = input("Approve trade? [y/N]: ").strip().lower()
            except EOFError:
                return False
            return answer in ("y", "yes")

        # Establish a baseline so we only react to NEW replies.
        baseline = self._call("getUpdates", {"timeout": 0}, timeout=20)
        offset = 0
        if baseline and baseline.get("result"):
            offset = baseline["result"][-1]["update_id"] + 1

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            data = self._call("getUpdates", {"offset": offset, "timeout": 10}, timeout=20)
            if data and data.get("result"):
                for update in data["result"]:
                    offset = update["update_id"] + 1
                    msg = (update.get("message") or {}).get("text", "").strip().lower()
                    if msg.startswith("/approve"):
                        self.send_message("✅ Trade approved — placing order.")
                        return True
                    if msg.startswith("/reject"):
                        self.send_message("❌ Trade rejected.")
                        return False
            time.sleep(_POLL_INTERVAL_SECONDS)

        self.send_message("⏳ No reply within window — auto-rejecting (safe default).")
        return False
