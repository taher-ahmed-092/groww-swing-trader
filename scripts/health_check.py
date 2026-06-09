"""
Standalone health check. Verifies the repo is in a safe, importable state.

    uv run python scripts/health_check.py

Exits 0 if all critical checks pass, 1 otherwise.
"""
from __future__ import annotations

import importlib
import os
import sys

# Run-from-anywhere: ensure the repo root (parent of scripts/) is importable.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

console = Console()

KEY_MODULES = [
    "config.risk_limits",
    "config.settings",
    "src.orchestrator.state",
    "src.orchestrator.graph",
    "src.risk.checker",
    "src.judge.evaluator",
    "src.broker.base",
    "src.broker.paper",
    "src.agents.scout.agent",
    "src.agents.fundamental.agent",
    "src.agents.fundamental.ratios",
    "src.agents.technical.agent",
    "src.agents.technical.indicators",
    "src.agents.executor.agent",
    "src.data.fetcher",
    "src.memory.journal",
    "src.memory.reflection",
    "src.memory.lessons",
    "src.memory.rca",
    "src.memory.daily_synthesis",
    "src.memory.weekly_distillation",
    "src.portfolio.ledger",
    "src.backtest.runner",
    "src.data.screener",
    "src.data.market_context",
    "src.notifications.telegram_bot",
    "src.analytics.performance",
    "src.agents.sentiment.agent",
]


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    # 1. Python version.
    py_ok = sys.version_info >= (3, 11)
    results.append(("Python >= 3.11", py_ok, f"{sys.version_info.major}.{sys.version_info.minor}"))

    # 2. Import every key module.
    for mod in KEY_MODULES:
        try:
            importlib.import_module(mod)
            results.append((f"import {mod}", True, "OK"))
        except Exception as exc:  # noqa: BLE001 - surface any import failure
            results.append((f"import {mod}", False, f"{type(exc).__name__}: {exc}"))

    # 3. .env.example present.
    results.append((".env.example exists", os.path.exists(".env.example"), ""))

    # 4. Live trading defaults OFF.
    try:
        from config.settings import settings

        live_off = settings.live_trading_enabled is False
        results.append(
            ("LIVE_TRADING_ENABLED is False", live_off, f"mode={settings.broker_mode}")
        )
    except Exception as exc:  # noqa: BLE001
        results.append(("LIVE_TRADING_ENABLED is False", False, str(exc)))

    # 5. data/ and logs/ dirs exist (create if missing).
    for d in ["data", "logs", "data/journal", "data/cache"]:
        os.makedirs(d, exist_ok=True)
    results.append(("data/ and logs/ dirs", True, "ensured"))

    # 6. Summary table.
    table = Table(title="groww-swing-trader health check")
    table.add_column("Check", style="cyan", no_wrap=True)
    table.add_column("Status")
    table.add_column("Detail", style="dim")

    all_ok = True
    for name, ok, detail in results:
        all_ok = all_ok and ok
        table.add_row(name, "[green]OK[/green]" if ok else "[red]FAIL[/red]", str(detail))

    console.print(table)

    if all_ok:
        console.print("[green]✓ All checks passed — paper mode is safe to run.[/green]")
        return 0
    console.print("[red]✗ Some checks failed — see table above.[/red]")
    return 1


if __name__ == "__main__":
    sys.exit(main())
