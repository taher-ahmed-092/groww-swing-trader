"""
Setup status checker — one clean table of what's configured and what's missing.

    uv run python scripts/verify_setup.py

Read-only. Never writes, never trades. Always exits 0 (it's a status report, not a
gate) unless something genuinely crashes.
"""
from __future__ import annotations

import os
import socket
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from config.settings import settings  # noqa: E402

console = Console()


def check_port(port: int) -> bool:
    try:
        s = socket.socket()
        s.settimeout(1)
        s.connect(("localhost", port))
        s.close()
        return True
    except OSError:
        return False


def main() -> int:
    t = Table(title="🌌 Cosmic Punk — Setup Status", border_style="cyan")
    t.add_column("Component", style="bold", width=26)
    t.add_column("Status", width=8)
    t.add_column("Details", width=46)

    def row(name: str, ok: bool, detail: str, required: bool = True) -> None:
        icon = "✅" if ok else ("❌" if required else "⏳")
        color = "green" if ok else ("red" if required else "yellow")
        t.add_row(name, f"[{color}]{icon}[/]", detail)

    has_ant = bool(settings.anthropic_api_key)
    row("Anthropic API key", has_ant,
        "LLM pipeline active" if has_ant else "→ console.anthropic.com ($5 credit)",
        required=False)

    row("Telegram bot token", bool(settings.telegram_bot_token),
        "bot ready" if settings.telegram_bot_token else "→ @BotFather /mybots")
    row("Telegram chat ID", settings.telegram_chat_id_int is not None,
        f"ID: {settings.telegram_chat_id}" if settings.telegram_chat_id_int is not None
        else "→ add TELEGRAM_CHAT_ID to .env")

    row("Finnhub API key", bool(settings.finnhub_api_key),
        "News + insider data active" if settings.finnhub_api_key else "→ finnhub.io (free)")
    row("NewsAPI key", bool(settings.news_api_key),
        "Global news active" if settings.news_api_key else "→ newsapi.org (free)")

    dash_token = bool(settings.dashboard_secret_token)
    dash_running = check_port(settings.dashboard_port)
    row("Dashboard token", dash_token,
        "Token set" if dash_token else "→ secrets.token_urlsafe(32)")
    row("Dashboard server", dash_running,
        f"Running on :{settings.dashboard_port}" if dash_running
        else "→ uv run python scripts/run_dashboard.py", required=False)

    try:
        from src.memory.journal import TradingJournal

        j = TradingJournal()
        n_trades = len(j.get_recent(n=200))
        n_kb = len(j.get_active_knowledge(min_confidence=0.0))
        row("Database", True, f"{n_trades} trades, {n_kb} knowledge patterns")
    except Exception as exc:
        row("Database", False, str(exc)[:42])

    try:
        from src.learning.intraday_simulator import IntradaySimulator

        s = IntradaySimulator().get_summary()
        row("Intraday simulator", True,
            f"{s['total']} sims, {s['win_rate']}% win rate", required=False)
    except Exception as exc:
        row("Intraday simulator", False, str(exc)[:42], required=False)

    row("Groww API (live trading)", bool(settings.has_groww_credentials),
        "Live trading ready" if settings.has_groww_credentials
        else "→ groww.in/trade-api when ready", required=False)

    console.print(t)

    if not has_ant:
        console.print("\n[bold yellow]NEXT STEP:[/] Get an Anthropic API key at "
                      "console.anthropic.com, add $5 credit, put it in .env — the full "
                      "LLM pipeline then activates.")
    elif settings.has_telegram and not dash_running:
        console.print("\n[bold yellow]NEXT STEP:[/] uv run python scripts/start_everything.py")
    else:
        console.print("\n[bold green]✅ Core setup complete. Run: "
                      "uv run python scripts/start_everything.py[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
