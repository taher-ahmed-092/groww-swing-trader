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
import socket
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


def _dns_resolves(host: str = "api.telegram.org") -> bool:
    try:
        socket.gethostbyname(host)
        return True
    except OSError:
        return False


def _ensure_dns_resolves() -> None:
    """WSL loses its DNS resolver config on every Windows sleep/resume cycle
    (resolv.conf points at a stale WSL-NAT nameserver), silently breaking
    Telegram/GitHub connectivity on restart until the user manually writes
    public nameservers to /etc/resolv.conf. Do that automatically instead."""
    if _dns_resolves():
        console.print("[dim]DNS check: ok.[/]")
        return

    console.print("[yellow]DNS resolution failed (api.telegram.org) — "
                  "likely a WSL/Windows sleep-cycle resolver reset. "
                  "Writing public nameservers to /etc/resolv.conf…[/]")
    try:
        Path("/etc/resolv.conf").write_text("nameserver 8.8.8.8\nnameserver 8.8.4.4\n")
    except OSError as exc:
        console.print(f"[red]DNS auto-fix failed: could not write /etc/resolv.conf ({exc}). "
                      "Tunnel/Telegram connectivity may be unavailable.[/]")
        return

    if _dns_resolves():
        console.print("[green]DNS auto-fix worked — resolution restored.[/]")
    else:
        console.print("[red]DNS auto-fix did not restore resolution. "
                      "Tunnel/Telegram connectivity may be unavailable.[/]")


def main() -> int:
    console.print(Panel(
        "[bold cyan]🌌 COSMIC PUNK TRADING SYSTEM[/]\n[dim]Starting all services…[/]",
        border_style="cyan"))

    _ensure_dns_resolves()

    # 1. Dashboard server
    console.print("[cyan]1/3 Starting dashboard server…[/]")
    try:
        processes.append(subprocess.Popen(
            [sys.executable, "scripts/run_dashboard.py"], cwd=_REPO_ROOT))
    except Exception as exc:
        console.print(f"[yellow]Dashboard failed to start: {exc} — continuing.[/]")
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
                candidate = match.group(0)
                # cloudflared's own API host (api.trycloudflare.com) is not a
                # per-tunnel subdomain — it surfaces here when DNS failed
                # mid-setup and the CLI fell back to printing its own API
                # base URL instead of a real tunnel hostname. Treating it as
                # a working public URL would show a link that 404s.
                if re.fullmatch(r"https://api\.trycloudflare\.com(/.*)?", candidate):
                    console.print(
                        "[yellow]⚠️ Tunnel failed (network issue) — dashboard only "
                        f"reachable at http://localhost:{settings.dashboard_port} "
                        "until network recovers[/]")
                    break
                tunnel_url = candidate
                break
    except FileNotFoundError:
        console.print("[yellow]cloudflared not installed — skipping tunnel (local only).[/]")

    # 3. Scheduler + Telegram bot
    console.print("[cyan]3/3 Starting scheduler + Telegram bot…[/]")
    try:
        processes.append(subprocess.Popen([sys.executable, "runner.py"], cwd=_REPO_ROOT))
    except Exception as exc:
        console.print(f"[yellow]Runner failed to start: {exc} — continuing.[/]")
    time.sleep(5)  # let the runner come up before announcing

    token = settings.dashboard_secret_token
    local_url = f"http://localhost:{settings.dashboard_port}/dashboard/{token}"
    full_url = f"{tunnel_url}/dashboard/{token}" if (tunnel_url and token) else None

    if full_url:
        try:
            url_file = Path("data/cache/current_dashboard_url.txt")
            url_file.parent.mkdir(parents=True, exist_ok=True)
            url_file.write_text(full_url)
        except OSError:
            pass

    # Comprehensive startup message to Telegram (best-effort — never crashes startup).
    try:
        from src.data.regime_detector import RegimeDetector
        from src.notifications.telegram_bot import TelegramNotifier
        from src.utils.tips import get_random_tip

        try:
            regime = RegimeDetector().detect()
        except Exception:
            regime = {"regime": "UNKNOWN", "rsi": 0}
        dash_line = (f"📱 Dashboard: {full_url}" if full_url
                     else "📱 Dashboard: local only (start cloudflared separately)")
        notifier = TelegramNotifier()
        if notifier.is_configured():
            notifier.send_message(
                "🌌 *Cosmic Punk Trading System — ONLINE*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Mode: {settings.mode_label}\n"
                f"Regime: {regime.get('regime', 'UNKNOWN')} | "
                f"RSI: {(regime.get('rsi') or 0):.0f}\n\n"
                f"{dash_line}\n\n"
                "Type /start to see all commands\n"
                "Type /scan to scan stocks now\n"
                "Type /pairs for market-neutral opportunities\n\n"
                f"💡 _{get_random_tip()}_"
            )
    except Exception as exc:
        console.print(f"[yellow]Telegram startup message failed: {exc}[/]")

    if full_url:
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

    # Immediate action on startup rather than waiting for the next scheduled
    # slot: if the market is open right now, run a real scan immediately; if
    # closed, kick off one continuous-simulation batch so learning starts
    # right away instead of waiting up to 30 minutes for the cron job.
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        ist = ZoneInfo("Asia/Kolkata")
        now = datetime.now(ist)
        is_weekday = now.weekday() < 5
        market_open = is_weekday and (9 * 60 + 15 <= now.hour * 60 + now.minute <= 15 * 60 + 30)

        if market_open:
            console.print("[cyan]Market is OPEN — running immediate scan…[/]")
            # Regime-aware dossier warm-up (Fix 3c): re-check stocks we lost on
            # last time for a favorable technical shift before today's scan runs,
            # so ScoutAgent's DOSSIER_RECOVERY_CANDIDATE boost has fresh data.
            try:
                from src.memory.company_dossier import find_recovery_candidates

                recovered = find_recovery_candidates()
                if recovered:
                    console.print(f"[cyan]Dossier warm-up: {len(recovered)} recovery candidate(s): "
                                  f"{', '.join(recovered[:10])}[/]")
            except Exception as exc:
                console.print(f"[yellow]Dossier warm-up failed: {exc}[/]")
            # Timeouts raised (was 120s/180s): a full --scan fetches daily+weekly
            # history, relative-strength vs Nifty, and affordability checks across
            # 241 watchlist symbols — even with the fetcher cache + parallel scoring
            # (Fix 2), that legitimately needs more headroom than 180s allowed.
            try:
                pairs_result = subprocess.run(
                    [sys.executable, "scripts/run_now.py", "--pairs"],
                    cwd=_REPO_ROOT, capture_output=True, text=True, timeout=240)
                console.print(pairs_result.stdout[-500:] if pairs_result.stdout else "No output")
            except subprocess.TimeoutExpired as exc:
                reached = exc.stdout[-500:] if exc.stdout else "no output captured"
                console.print(f"[yellow]--pairs timed out after 240s. Last output reached:\n{reached}[/yellow]")
            try:
                scan_result = subprocess.run(
                    [sys.executable, "scripts/run_now.py", "--scan"],
                    cwd=_REPO_ROOT, capture_output=True, text=True, timeout=420)
                console.print(scan_result.stdout[-300:] if scan_result.stdout else "No output")
            except subprocess.TimeoutExpired as exc:
                reached = exc.stdout[-500:] if exc.stdout else "no output captured"
                console.print(f"[yellow]--scan timed out after 420s. Last output reached:\n{reached}[/yellow]")
        else:
            console.print(
                "[yellow]Market CLOSED — market opens 9:15 AM IST weekdays. "
                "Continuous simulation running until then.[/]")
            subprocess.Popen(
                [sys.executable, "-c",
                 "from src.learning.continuous_simulator import ContinuousSimulator; "
                 "r = ContinuousSimulator().run_batch(); print(f'Sim batch: {r}')"],
                cwd=_REPO_ROOT)
    except Exception as exc:
        console.print(f"[yellow]Startup scan/sim failed: {exc} — continuing.[/]")

    # Surface current learning recommendations immediately rather than making
    # the user go find and open LESSONS.md themselves.
    try:
        lessons = Path("LESSONS.md")
        if lessons.exists():
            content = lessons.read_text()
            if "## Recommendations" in content:
                start = content.find("## Recommendations")
                end = content.find("\n##", start + 1)
                rec_section = content[start:end if end > 0 else start + 500]
                console.print(Panel(
                    rec_section.replace("## Recommendations", "").strip(),
                    title="[cyan]Current Learning Recommendations[/]",
                    border_style="cyan"))
    except Exception as exc:
        console.print(f"[yellow]Could not read LESSONS.md recommendations: {exc}[/]")

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
