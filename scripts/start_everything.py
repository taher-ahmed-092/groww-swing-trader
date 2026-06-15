"""
One-command startup: dashboard + Cloudflare tunnel + scheduler/runner.

    uv run python scripts/start_everything.py

Starts three child processes, captures the (restart-changing) Cloudflare tunnel
URL, and pushes the full dashboard link to your Telegram bot so you can open it on
your phone. Ctrl+C (or a KILL_SWITCH file) tears everything down cleanly.

cloudflared is optional — if it isn't installed the dashboard still runs locally.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402

from config.settings import settings  # noqa: E402

console = Console()
processes: list[subprocess.Popen] = []


def cleanup(sig=None, frame=None):
    for p in processes:
        try:
            p.terminate()
        except Exception:
            pass
    console.print("\n[yellow]All processes stopped.[/yellow]")
    sys.exit(0)


signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)


def main() -> int:
    console.print(Panel(
        "[bold cyan]🌌 COSMIC PUNK TRADING SYSTEM[/]\n[dim]Starting all services…[/]",
        border_style="cyan"))

    # 1. Dashboard server
    console.print("[cyan]1/3 Starting dashboard server…[/]")
    processes.append(subprocess.Popen([sys.executable, "scripts/run_dashboard.py"], cwd=_REPO_ROOT))
    time.sleep(3)

    # 2. Cloudflare tunnel (optional)
    tunnel_url = None
    try:
        console.print("[cyan]2/3 Starting Cloudflare tunnel…[/]")
        tunnel_proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url",
             f"http://localhost:{settings.dashboard_port}", "--no-autoupdate"],
            cwd=_REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        processes.append(tunnel_proc)
        deadline = time.time() + 30
        while time.time() < deadline:
            line = tunnel_proc.stderr.readline() if tunnel_proc.stderr else ""
            if not line and tunnel_proc.poll() is not None:
                break
            match = re.search(r"https://\S+\.trycloudflare\.com", line or "")
            if match:
                tunnel_url = match.group(0)
                break
    except FileNotFoundError:
        console.print("[yellow]cloudflared not installed — skipping tunnel (local only).[/]")

    # 3. Scheduler + Telegram bot
    console.print("[cyan]3/3 Starting scheduler + Telegram bot…[/]")
    processes.append(subprocess.Popen([sys.executable, "runner.py"], cwd=_REPO_ROOT))
    time.sleep(2)

    token = settings.dashboard_secret_token
    local_url = f"http://localhost:{settings.dashboard_port}/dashboard/{token}"

    if tunnel_url and token:
        full_url = f"{tunnel_url}/dashboard/{token}"
        url_file = Path("data/cache/current_dashboard_url.txt")
        url_file.parent.mkdir(parents=True, exist_ok=True)
        url_file.write_text(full_url)
        try:
            from src.notifications.telegram_bot import TelegramNotifier

            notifier = TelegramNotifier()
            if notifier.is_configured():
                notifier.send_message(
                    "🚀 *Cosmic Punk is Live!*\n\n"
                    f"📊 Dashboard:\n`{full_url}`\n\n"
                    "_Tap the link to open on your phone._")
        except Exception as exc:
            console.print(f"[yellow]Telegram send failed: {exc}[/]")
        console.print(Panel(
            "[bold green]✅ EVERYTHING IS RUNNING[/]\n\n"
            f"[bold]Laptop:[/]  {local_url}\n"
            f"[bold]Phone:[/]   [cyan]{full_url}[/]\n\n"
            "[dim]URL sent to your Telegram bot.[/]\n[dim]Ctrl+C to stop all services.[/]",
            border_style="green", title="🌌 Cosmic Punk Trading System"))
    else:
        console.print(Panel(
            "[bold green]✅ RUNNING (local only)[/]\n\n"
            f"[bold]Dashboard:[/] {local_url}\n\n"
            "[dim]No tunnel URL — install cloudflared for phone access.[/]\n"
            "[dim]Ctrl+C to stop all services.[/]",
            border_style="green"))

    try:
        while True:
            time.sleep(10)
            if Path("KILL_SWITCH").exists():
                console.print("[red]KILL_SWITCH detected — stopping all.[/]")
                cleanup()
    except KeyboardInterrupt:
        cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
