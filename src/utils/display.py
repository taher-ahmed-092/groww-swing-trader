"""Rich display helpers — color-coded panels and tables for the CLI scripts."""
from __future__ import annotations

from rich.panel import Panel
from rich.table import Table

from config.settings import settings
from src.utils.tips import get_random_tip


def _bar(value: float, maximum: float = 10.0, width: int = 10) -> str:
    filled = int(round((value / maximum) * width)) if maximum else 0
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def startup_panel() -> Panel:
    api = "✅ set" if settings.has_anthropic_key else "❌ none (demo)"
    body = (
        f"[bold]Mode:[/bold] {settings.mode_label}\n"
        f"[bold]Anthropic key:[/bold] {api}\n"
        f"[bold]Broker:[/bold] {settings.broker_mode}\n\n"
        f"💡 {get_random_tip()}"
    )
    return Panel(body, title="🚀 groww-swing-trader", border_style="cyan")


def positions_table(rows: list[dict]) -> Table:
    table = Table(title="📊 Open Positions", border_style="blue")
    for col in ("Symbol", "Entry", "Current", "Stop", "Target", "P&L %", "Status"):
        table.add_column(col)
    for r in rows:
        pnl = r.get("pnl_pct", 0) or 0
        color = "green" if pnl >= 0 else "red"
        table.add_row(
            r.get("symbol", "?"),
            f"{r.get('entry', 0):.2f}",
            f"{r.get('current', 0):.2f}",
            f"{r.get('stop', 0):.2f}",
            f"{r.get('target', 0):.2f}",
            f"[{color}]{pnl:+.2f}%[/{color}]",
            r.get("status", ""),
        )
    return table


def knowledge_table(entries: list) -> Table:
    table = Table(title="🧠 Knowledge Base", border_style="magenta")
    for col in ("Pattern", "Conf", "Seen", "Type"):
        table.add_column(col)
    for e in entries:
        tag = "hypothesis" if getattr(e, "is_hypothesis", False) else "rule"
        table.add_row(
            (e.pattern_description or "")[:48],
            f"{e.confidence:.2f}",
            str(e.observed_count),
            tag,
        )
    return table


def trade_result_panel(symbol: str, decision: str, detail: str) -> Panel:
    color = {"APPROVED": "green", "REJECTED": "red"}.get(decision, "yellow")
    return Panel(detail, title=f"[{color}]{symbol} — {decision}[/{color}]", border_style=color)
