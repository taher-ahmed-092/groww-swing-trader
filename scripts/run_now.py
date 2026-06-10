"""
Manual runner — scout, scan, or analyze a single symbol with full output.

    uv run python scripts/run_now.py --scout-only
    uv run python scripts/run_now.py --scan
    uv run python scripts/run_now.py --symbol RELIANCE
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402

from config.risk_limits import LIMITS  # noqa: E402
from src.agents.scout.agent import ScoutAgent  # noqa: E402
from src.data.regime_detector import RegimeDetector  # noqa: E402
from src.orchestrator.graph import app  # noqa: E402
from src.orchestrator.state import get_initial_state  # noqa: E402
from src.utils.display import startup_panel  # noqa: E402

console = Console()


def _kill_switch_active() -> bool:
    return os.path.exists(LIMITS.kill_switch_file)


def _scout_table(candidates: list[dict], regime: dict) -> Table:
    table = Table(title=f"🔍 Scout — regime: {regime.get('regime', '?')}", border_style="cyan")
    for col in ("Symbol", "Sector", "Score", "Flags", "Rationale"):
        table.add_column(col, overflow="fold")
    for c in candidates:
        score = c.get("score", 0)
        color = "green" if score >= 6 else "yellow" if score >= 3 else "red"
        table.add_row(
            c.get("symbol", "?"), c.get("sector", "?"),
            f"[{color}]{score}[/{color}]",
            ", ".join(c.get("flags", [])) or "—",
            (c.get("rationale", "") or "")[:60],
        )
    return table


def _run_pipeline(symbol: str) -> dict:
    state = get_initial_state(symbol)
    final = app.invoke(state, config={"configurable": {"thread_id": f"run-{symbol}"}})
    return final


def _results_table(rows: list[dict]) -> Table:
    table = Table(title="📋 Pipeline Results", border_style="blue")
    for col in ("Symbol", "Decision", "Entry", "Stop", "Target", "R:R", "Size ₹", "Reason"):
        table.add_column(col, overflow="fold")
    for r in rows:
        decision = r["decision"]
        color = "green" if decision == "APPROVED" else "red"
        table.add_row(
            r["symbol"], f"[{color}]{decision}[/{color}]",
            f"{r['entry']:.2f}", f"{r['stop']:.2f}", f"{r['target']:.2f}",
            f"1:{r['rr']:.1f}", f"{r['size']:.0f}", r["reason"][:50],
        )
    return table


def _summarize(symbol: str, final: dict) -> dict:
    tech = final.get("technical_verdict", {})
    risk = final.get("risk_check", {})
    judge = final.get("judge_verdict", {})
    entry = tech.get("entry_price", 0) or 0
    stop = tech.get("stop_price", 0) or 0
    target = tech.get("target_price", 0) or 0
    rr = (target - entry) / (entry - stop) if (entry - stop) > 0 else 0
    approved = bool(risk.get("approved"))
    reason = (risk.get("reasons", ["—"])[0] if not approved
              else risk.get("sizing_explanation", judge.get("one_line_verdict", "approved")))
    return {
        "symbol": symbol,
        "decision": "APPROVED" if approved else "REJECTED",
        "entry": entry, "stop": stop, "target": target, "rr": rr,
        "size": risk.get("position_size_inr", 0) or 0,
        "reason": reason,
    }


def _all_flags(finals: list[dict]) -> set[str]:
    flags: set[str] = set()
    for f in finals:
        flags.update(f.get("judge_verdict", {}).get("flags", []) or [])
    return flags


def _print_entry_windows(finals: list[dict]) -> None:
    """For approved trades, show the optimal entry window + live countdown."""
    for f in finals:
        if not f.get("risk_check", {}).get("approved"):
            continue
        tw = f.get("time_window", {})
        if not tw:
            continue
        urgent = " 🚨 ACT QUICKLY" if tw.get("urgency") == "HIGH" else ""
        body = (
            f"{tw['window_open']} – {tw['window_close']} IST ({tw['window_label']})\n"
            f"{tw['countdown_display']}{urgent}\n"
            f"Rationale: {tw['rationale']}"
        )
        console.print(Panel(body, title=f"⏰ ENTRY WINDOW — {f.get('symbol', '?')}",
                            border_style="green"))


def _print_why_no_trade(finals: list[dict], regime: dict) -> None:
    """If every candidate was rejected, explain the market conditions plainly."""
    if not finals:
        return
    approved = any(f.get("risk_check", {}).get("approved") for f in finals)
    if approved:
        return

    flags = _all_flags(finals)
    mc = finals[0].get("market_context", {})
    rsi = mc.get("nifty_rsi") or 0
    ma50 = (regime.get("ma50") or 0)

    if "FIGHTING_NIFTY" in flags or mc.get("nifty_trend") == "DOWNTREND":
        body = (
            f"Nifty is in DOWNTREND (RSI {rsi:.0f}).\n"
            "The system correctly avoids buying into a falling market.\n"
            "This is not a failure — this is capital protection.\n"
            "When Nifty recovers above MA50 and RSI rises above 50,\n"
            "BUY signals will start appearing.\n"
            f"💡 What to watch: Nifty above {ma50:.0f} AND RSI > 50"
        )
        console.print(Panel(body, title="📉 WHY NO TRADES TODAY", border_style="yellow"))
    elif "CHOPPY_MARKET" in flags:
        console.print(Panel(
            "ADX is below threshold — market lacks clear direction.\n"
            "Choppy markets produce false signals.\n"
            "Waiting for ADX > 20 before entering.",
            title="🌀 WHY NO TRADES TODAY", border_style="yellow",
        ))
    else:
        console.print(Panel(
            "No candidate cleared every gate today (confidence / risk / gut check).\n"
            "The system only acts on high-conviction setups.",
            title="ℹ️ WHY NO TRADES TODAY", border_style="yellow",
        ))
    console.print("[green]✅ System is working correctly. No action needed from you.[/green]")


def main() -> int:
    parser = argparse.ArgumentParser(description="groww-swing-trader manual runner")
    parser.add_argument("--scout-only", action="store_true", help="show scout results only")
    parser.add_argument("--scan", action="store_true", help="full pipeline on top 2 candidates")
    parser.add_argument("--symbol", type=str, help="full pipeline on a single symbol")
    parser.add_argument("--demo", action="store_true", help="(demo mode is auto when no key)")
    args = parser.parse_args()

    console.print(startup_panel())

    if _kill_switch_active():
        console.print("[bold red]KILL_SWITCH present — halting. Remove the file to proceed.[/bold red]")
        return 1

    regime = RegimeDetector().detect()
    console.print(f"[dim]Regime: {regime['regime']} — {regime['strategy']}[/dim]")

    if args.symbol:
        final = _run_pipeline(args.symbol)
        console.print(_results_table([_summarize(args.symbol, final)]))
        _print_entry_windows([final])
        _print_why_no_trade([final], regime)
        return 0

    candidates = ScoutAgent().scan()
    console.print(_scout_table(candidates, regime))

    if args.scout_only or not (args.scan or args.symbol):
        return 0

    rows, finals = [], []
    for c in candidates[:2]:
        console.print(f"\n[cyan]Running full pipeline for {c['symbol']}…[/cyan]")
        final = _run_pipeline(c["symbol"])
        finals.append(final)
        rows.append(_summarize(c["symbol"], final))
    console.print(_results_table(rows))
    console.print("[dim]This is what the system decided and WHY (see Reason column).[/dim]")
    _print_entry_windows(finals)
    _print_why_no_trade(finals, regime)
    return 0


if __name__ == "__main__":
    sys.exit(main())
