"""
Matplotlib chart generators → PNG bytes for Telegram. Dark theme, never raises.
Uses the non-interactive Agg backend (no display needed on a server).
"""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def set_dark_style() -> None:
    plt.rcParams.update({
        "figure.facecolor": "#0a0e17", "axes.facecolor": "#111827",
        "axes.edgecolor": "#1e293b", "axes.labelcolor": "#e2e8f0",
        "xtick.color": "#64748b", "ytick.color": "#64748b",
        "text.color": "#e2e8f0", "grid.color": "#1e293b", "grid.alpha": 0.5,
    })


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=150)
    plt.close(fig)
    return buf.getvalue()


def generate_performance_chart(trades: list) -> bytes:
    set_dark_style()
    fig, ax = plt.subplots(figsize=(10, 4))
    fig.patch.set_facecolor("#0a0e17")
    if not trades:
        ax.text(0.5, 0.5, "No closed trades yet", ha="center", va="center",
                transform=ax.transAxes, color="#64748b", fontsize=14)
        return _png(fig)

    rows = trades[-20:]
    symbols = [t["symbol"][:6] for t in rows]
    pnls = [t["pnl_pct"] for t in rows]
    colors = ["#10b981" if p >= 0 else "#ef4444" for p in pnls]
    ax.bar(range(len(symbols)), pnls, color=colors, alpha=0.85, width=0.7)
    ax.axhline(0, color="#64748b", linewidth=0.8, linestyle="--")
    ax.set_xticks(range(len(symbols)))
    ax.set_xticklabels(symbols, rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("P&L %", fontsize=10)
    ax.set_title("Trade Performance — Last 20 Trades", fontsize=12,
                 fontweight="bold", color="#00d4ff", pad=12)
    ax.grid(axis="y", alpha=0.3)
    total = sum(pnls)
    ax.text(0.98, 0.97, f"Total: {total:+.1f}%", transform=ax.transAxes, ha="right",
            va="top", color="#10b981" if total >= 0 else "#ef4444", fontsize=11,
            fontweight="bold")
    plt.tight_layout()
    return _png(fig)


def generate_win_rate_chart(summary: dict) -> bytes:
    set_dark_style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
    fig.patch.set_facecolor("#0a0e17")
    total = summary.get("total_trades", 0)
    wr = summary.get("win_rate", 0) / 100
    wins = round(total * wr)
    losses = total - wins
    if total > 0:
        ax1.pie([wins, losses], colors=["#10b981", "#ef4444"], startangle=90,
                wedgeprops={"width": 0.5, "edgecolor": "#0a0e17"})
        ax1.text(0, 0, f"{wr * 100:.0f}%\nWin Rate", ha="center", va="center",
                 fontsize=14, fontweight="bold", color="#00d4ff")
    ax1.set_title("Win/Loss Split", color="#e2e8f0")
    avg_win = summary.get("avg_win_pct", 0)
    avg_loss = abs(summary.get("avg_loss_pct", 0))
    ax2.bar(["Avg Win", "Avg Loss"], [avg_win, avg_loss],
            color=["#10b981", "#ef4444"], alpha=0.85, width=0.4)
    ax2.set_title("Average Win vs Loss", color="#e2e8f0")
    for i, v in enumerate([avg_win, avg_loss]):
        ax2.text(i, v + 0.1, f"{v:.1f}%", ha="center", fontsize=11,
                 fontweight="bold", color="#e2e8f0")
    plt.tight_layout()
    return _png(fig)


def generate_knowledge_chart(entries: list) -> bytes:
    if not entries:
        return b""
    set_dark_style()
    entries = entries[:8]
    labels = [(e["description"][:35] + "...") if len(e["description"]) > 35
              else e["description"] for e in entries]
    confidences = [e["confidence"] for e in entries]
    colors = ["#10b981" if c >= 0.7 else "#f59e0b" if c >= 0.4 else "#64748b"
              for c in confidences]
    fig, ax = plt.subplots(figsize=(10, 4))
    fig.patch.set_facecolor("#0a0e17")
    bars = ax.barh(labels, confidences, color=colors, alpha=0.85)
    ax.set_xlim(0, 1)
    ax.set_xlabel("Confidence Score", color="#e2e8f0")
    ax.set_title("🧠 Knowledge Base — Top Patterns", color="#00d4ff",
                 fontsize=12, fontweight="bold")
    ax.axvline(0.7, color="#10b981", linewidth=1, linestyle="--", alpha=0.5)
    for bar, conf in zip(bars, confidences):
        ax.text(conf + 0.01, bar.get_y() + bar.get_height() / 2, f"{conf:.2f}",
                va="center", fontsize=9, color="#e2e8f0")
    plt.tight_layout()
    return _png(fig)
