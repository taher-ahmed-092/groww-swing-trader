"""
Central visual library — rich-based terminal UX for groww-swing-trader.

Visual feedback builds trust in an autonomous system: every interaction should make
it obvious what the machine decided and why. Pure rendering helpers; no trading logic.
"""
from __future__ import annotations

import random

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

console = Console()

# ── Startup splash ───────────────────────────────────────────────────────────
SPLASH = r"""
   ██████╗ ██████╗  ██████╗ ██╗    ██╗██╗    ██╗
  ██╔════╝██╔═══██╗██╔═══██╗██║    ██║██║    ██║
  ██║     ██║   ██║██║   ██║██║ █╗ ██║██║ █╗ ██║
  ██║     ██║   ██║██║   ██║██║███╗██║██║███╗██║
  ╚██████╗╚██████╔╝╚██████╔╝╚███╔███╔╝╚███╔███╔╝
   ╚═════╝ ╚═════╝  ╚═════╝  ╚══╝╚══╝  ╚══╝╚══╝
         SWING TRADER  ·  COSMIC PUNK EDITION
"""

TAGLINES = [
    "The machine never sleeps. Neither does your edge.",
    "Numbers don't lie. Patterns don't forget.",
    "Signal over noise. Always.",
    "Every rejection is capital saved.",
    "The market is a test. Your system is the answer.",
    "Patience encoded. Discipline automated.",
    "Buy the signal, not the emotion.",
    "In downtrends we wait. In uptrends we hunt.",
]

_MODE_COLOR = {"🔴 LIVE": "red", "📝 PAPER": "blue", "🧪 DEMO": "yellow"}


def print_splash(settings) -> None:
    mode = settings.mode_label
    c = _MODE_COLOR.get(mode, "green")
    console.print(f"[bold {c}]{SPLASH}[/]")
    console.print(
        Panel(
            f"[bold {c}]{mode}[/]  │  "
            f"[dim]API: {'✅ Active' if settings.has_anthropic_key else '⚠️  Demo mode'}[/]  │  "
            f"[dim]Broker: {settings.broker_mode}[/]\n\n"
            f"[italic cyan]{random.choice(TAGLINES)}[/]",
            border_style=c, padding=(0, 2),
        )
    )


# ── Pipeline animation ───────────────────────────────────────────────────────
def animate_pipeline(symbol: str) -> Progress:
    console.print(Rule(f"[bold cyan]Pipeline — {symbol}[/]"))
    return Progress(
        SpinnerColumn("dots2", style="cyan"),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=20, style="cyan", complete_style="green"),
        TextColumn("[dim]{task.fields[detail]}"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )


def show_node_result(node: str, result: str, verdict: str = "") -> None:
    icons = {"APPROVED": "✅", "REJECTED": "❌", "SKIPPED": "⏭️", "OK": "✓"}
    icon = icons.get(verdict, "◆")
    color = "green" if verdict == "APPROVED" else "red" if verdict == "REJECTED" else "cyan"
    console.print(f"  [{color}]{icon}[/]  [bold]{node:<22}[/] [dim]{result}[/]")


# ── Confidence visualisers ───────────────────────────────────────────────────
def confidence_gauge(score_0_10: float) -> str:
    score = score_0_10 or 0
    filled = max(0, min(10, round(score)))
    color = "bold green" if score >= 8 else "yellow" if score >= 6 else "red"
    bar = "█" * filled + "░" * (10 - filled)
    return f"[{color}]{bar}[/] {score:.1f}/10"


def mini_bar(value_0_1: float, width: int = 5) -> str:
    value = value_0_1 or 0
    filled = max(0, min(width, round(value * width)))
    return "▓" * filled + "░" * (width - filled)


# ── Trade result card ────────────────────────────────────────────────────────
def print_trade_card(state: dict, decision: str) -> None:
    symbol = state.get("symbol", "?")
    tech = state.get("technical_verdict", {})
    fund = state.get("fundamental_verdict", {})
    judge = state.get("judge_verdict", {})
    sizing = state.get("risk_check", {})
    tw = tech.get("time_window", {})
    entry = tech.get("entry_price", 0) or 0
    stop = tech.get("stop_price", 0) or 0
    target = tech.get("target_price", 0) or 0
    rr = ((target - entry) / (entry - stop)) if (entry - stop) > 0 else 0
    size = sizing.get("position_size_inr", 0) or 0
    reason = state.get("errors", [""])[0] if state.get("errors") else ""

    color = "green" if decision == "APPROVED" else "red"
    icon = "🚀" if decision == "APPROVED" else "🛑"

    content = (
        f"[bold]{icon}  {symbol}[/]  —  [dim]{decision}[/]\n\n"
        f"📊 Confidence  {confidence_gauge(judge.get('overall_score', 0))}\n"
        f"📈 Fund  {mini_bar(fund.get('score', 0))} {fund.get('score', 0) or 0:.2f}  "
        f"🔧 Tech  {mini_bar(tech.get('score', 0))} {tech.get('score', 0) or 0:.2f}  "
        f"🧑‍⚖️ {judge.get('overall_score', 0) or 0:.1f}/10\n\n"
    )
    if decision == "APPROVED":
        content += (
            f"💰 Entry ₹{entry:,.2f}   🛑 Stop ₹{stop:,.2f}   🎯 Target ₹{target:,.2f}\n"
            f"📐 R:R 1:{rr:.1f}   💸 Size ₹{size:.0f}   "
            f"[dim]{sizing.get('sizing_explanation', '')}[/]\n"
        )
        if tw:
            urgency_color = "red" if tw.get("urgency") == "HIGH" else "yellow"
            content += f"\n[{urgency_color}]{tw.get('countdown_display', '')}[/]\n"
    else:
        detail = reason or (judge.get("reasoning", "") or "")[:80]
        content += f"[dim]Reason: {detail}[/]\n"

    console.print(Panel(content, border_style=color, padding=(0, 2)))


# ── ASCII candlestick mini-chart ─────────────────────────────────────────────
def print_mini_chart(df, symbol: str) -> None:
    if df is None or len(df) < 5:
        return
    recent = df.tail(20).copy()
    closes = recent["Close"].tolist()
    opens = recent["Open"].tolist()
    price_min = float(recent["Low"].min())
    price_max = float(recent["High"].max())
    price_range = price_max - price_min
    if price_range == 0:
        return

    height = 8
    grid = [[" "] * len(closes) for _ in range(height)]
    for i, (o, c) in enumerate(zip(opens, closes)):
        row = int((1 - (c - price_min) / price_range) * (height - 1))
        row = max(0, min(height - 1, row))
        grid[row][i] = "▲" if c >= o else "▼"

    console.print(Rule(f"[bold cyan]{symbol} — Last 20 candles[/]"))
    for row_idx, row in enumerate(grid):
        price_at_row = price_max - (row_idx / (height - 1)) * price_range
        line = Text()
        line.append(f"₹{price_at_row:>9,.0f}  ", style="dim")
        for ch in row:
            if ch == "▲":
                line.append(ch, style="bold green")
            elif ch == "▼":
                line.append(ch, style="bold red")
            else:
                line.append("·", style="dim")
        console.print(line)
    console.print(Rule(style="dim"))


# ── Indicator summary panel ──────────────────────────────────────────────────
def print_indicator_panel(indicators: dict) -> None:
    t = Table(title="Technical Indicators", box=box.SIMPLE_HEAVY,
              show_header=True, header_style="bold cyan")
    t.add_column("Indicator", style="bold", width=22)
    t.add_column("Value", justify="right", width=12)
    t.add_column("Signal", width=20)

    def row(name, val, signal, signal_color="white"):
        t.add_row(name, str(val) if val is not None else "—", f"[{signal_color}]{signal}[/]")

    rsi = indicators.get("rsi_14")
    rsi_color = "green" if 50 <= (rsi or 0) <= 65 else "red" if (rsi or 0) > 70 else "yellow"
    row("RSI (14)", f"{rsi:.1f}" if rsi else "—", indicators.get("rsi_signal", "NEUTRAL"), rsi_color)

    trend = indicators.get("trend", "SIDEWAYS")
    row("Trend", trend, trend,
        "green" if trend == "UPTREND" else "red" if trend == "DOWNTREND" else "yellow")

    adx = indicators.get("adx_14")
    adx_sig = indicators.get("adx_signal", "NEUTRAL")
    row("ADX (14)", f"{adx:.1f}" if adx else "—", adx_sig,
        "green" if adx_sig == "TRENDING" else "red" if adx_sig == "CHOPPY" else "yellow")

    macd = indicators.get("macd") or 0
    row("MACD", f"{macd:.3f}", "▲ Positive" if macd > 0 else "▼ Negative",
        "green" if macd > 0 else "red")

    obv = indicators.get("obv_trend", "NEUTRAL")
    row("OBV Trend", obv, obv, "green" if obv == "RISING" else "red")

    supertrend = indicators.get("supertrend_direction", "NEUTRAL")
    row("Supertrend", indicators.get("supertrend", "—"), supertrend,
        "green" if supertrend == "BULLISH" else "red")

    ichimoku = indicators.get("ichimoku_signal", "NEUTRAL")
    row("Ichimoku", ichimoku, ichimoku, "green" if ichimoku == "BULLISH" else "red")

    vwap = indicators.get("price_vs_vwap", "—")
    row("vs VWAP", vwap, vwap, "green" if vwap == "ABOVE" else "red")

    cmf = indicators.get("cmf_signal", "NEUTRAL")
    row("CMF (20)", f"{indicators.get('cmf_20', 0) or 0:.3f}", cmf,
        "green" if "BUYING" in cmf else "red" if "SELLING" in cmf else "yellow")

    pattern = indicators.get("candlestick_pattern", "NONE")
    conf = indicators.get("candlestick_confidence", "NONE")
    row("Candle Pattern", pattern, conf,
        "green" if conf == "HIGH" else "yellow" if conf == "MEDIUM" else "dim")

    pos52 = indicators.get("week52_position", "—")
    row("52W Position", pos52, pos52)
    console.print(t)


# ── P&L sparkline ────────────────────────────────────────────────────────────
def print_pnl_sparkline(pnl_history: list) -> None:
    if not pnl_history:
        return
    blocks = " ▁▂▃▄▅▆▇█"
    mn, mx = min(pnl_history), max(pnl_history)
    rng = (mx - mn) or 1
    line = Text("P&L trend  ")
    for val in pnl_history[-40:]:
        idx = int((val - mn) / rng * (len(blocks) - 1))
        line.append(blocks[idx], style="green" if val >= 0 else "red")
    last = pnl_history[-1]
    line.append(f"  [{last:+.1f}%]", style="green" if last >= 0 else "red")
    console.print(line)


# ── Countdown bar ────────────────────────────────────────────────────────────
def print_countdown(minutes_remaining: int, window_label: str, urgency: str = "MEDIUM") -> None:
    minutes_remaining = minutes_remaining or 0
    pct = max(0, min(1, minutes_remaining / 90))
    filled = int(pct * 30)
    color = "red" if urgency == "HIGH" or minutes_remaining < 15 else "yellow"
    bar = "█" * filled + "░" * (30 - filled)
    console.print(
        Panel(
            f"[bold {color}]⏰  ENTRY WINDOW — {window_label}[/]\n"
            f"[{color}]{bar}[/]  [{color}]{minutes_remaining} min remaining[/]",
            border_style=color, padding=(0, 1),
        )
    )


# ── Knowledge base tree ──────────────────────────────────────────────────────
def print_knowledge_tree(entries: list) -> None:
    if not entries:
        console.print("[dim]Knowledge base empty — learning begins after first trades.[/]")
        return
    tree = Tree("[bold cyan]📚 Knowledge Base[/]")
    categories: dict[str, list] = {}
    for e in entries:
        categories.setdefault(e.category or "UNCATEGORISED", []).append(e)
    for cat, items in sorted(categories.items()):
        branch = tree.add(f"[bold yellow]{cat}[/] ({len(items)})")
        for item in sorted(items, key=lambda x: -x.confidence)[:5]:
            conf = item.confidence
            bar = "█" * round(conf * 10) + "░" * (10 - round(conf * 10))
            color = "green" if conf >= 0.7 else "yellow" if conf >= 0.4 else "dim"
            label = "(hypothesis)" if item.is_hypothesis else "(rule)"
            branch.add(
                f"[{color}]{bar}[/] {conf:.2f}  [white]{item.pattern_description[:55]}[/]  "
                f"[dim]{label} · {item.observed_count}× seen[/]"
            )
    console.print(tree)
