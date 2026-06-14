"""Entry point for the read-only web dashboard server."""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402

from config.settings import settings  # noqa: E402
from dashboard.server import run_dashboard  # noqa: E402

console = Console()


def main() -> int:
    token = settings.dashboard_secret_token
    port = settings.dashboard_port
    if not token:
        console.print(Panel(
            "[bold red]No DASHBOARD_SECRET_TOKEN set.[/]\n\n"
            "Generate one:\n"
            "  python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"\n"
            "Add it to .env as DASHBOARD_SECRET_TOKEN, then re-run.",
            border_style="red", title="Dashboard not configured"))
        return 1

    console.print(Panel(
        f"[bold cyan]🌐 COSMIC PUNK DASHBOARD[/]\n\n"
        f"[bold]Local URL:[/] http://localhost:{port}/dashboard/{token}\n"
        f"[dim](read-only — share this URL only with trusted people)[/]\n\n"
        f"[bold]Mobile access:[/] bash deploy/setup_cloudflare_tunnel.sh\n\n"
        f"[dim]Ctrl+C to stop[/]",
        border_style="cyan", title="Dashboard Starting"))
    run_dashboard()
    return 0


if __name__ == "__main__":
    sys.exit(main())
