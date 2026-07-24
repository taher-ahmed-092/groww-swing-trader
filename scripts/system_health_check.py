"""
Comprehensive system health check — every component, one Telegram report.

Runs automatically every 3 days (runner.py's triday_health_check_job) and via
the /health_check Telegram command. Read-only: no trading logic touched.

    uv run python scripts/system_health_check.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

IST = ZoneInfo("Asia/Kolkata")


def check_all() -> dict:
    results: dict = {}

    # 1. Data pipeline
    try:
        from src.data.fetcher import MarketDataFetcher

        df = MarketDataFetcher().get_price_history("RELIANCE", period="5d")
        results["data_pipeline"] = {"ok": df is not None and len(df) > 0,
                                    "rows": len(df) if df is not None else 0}
    except Exception as e:
        results["data_pipeline"] = {"ok": False, "error": str(e)[:50]}

    # 2. Indicator computation
    try:
        from src.agents.technical.indicators import compute_indicators
        from src.data.fetcher import MarketDataFetcher

        df = MarketDataFetcher().get_price_history("RELIANCE", period="1y")
        ind = compute_indicators(df)
        rsi = ind.get("rsi_14")
        results["indicators"] = {"ok": rsi is not None and 0 < rsi < 100,
                                 "rsi": rsi, "supertrend": ind.get("supertrend_direction")}
    except Exception as e:
        results["indicators"] = {"ok": False, "error": str(e)[:50]}

    # 3. Scout quality
    try:
        from src.agents.scout.agent import ScoutAgent

        candidates = ScoutAgent().scan()
        results["scout"] = {"ok": len(candidates) > 0, "candidates": len(candidates),
                            "top": candidates[0]["symbol"] if candidates else None}
    except Exception as e:
        results["scout"] = {"ok": False, "error": str(e)[:50]}

    # 4. Pairs trading
    try:
        from src.strategies.pairs_trading import PairsTradingStrategy

        opps = PairsTradingStrategy().find_opportunities()
        results["pairs"] = {"ok": True, "opportunities": len(opps),
                           "best_z": opps[0]["z_score"] if opps else 0}
    except Exception as e:
        results["pairs"] = {"ok": False, "error": str(e)[:50]}

    # 5. Knowledge base integrity
    try:
        from src.memory.journal import TradingJournal

        j = TradingJournal()
        kb = j.get_active_knowledge(min_confidence=0.0)
        wins = sum(1 for e in kb if "WON" in (e.pattern_description or "")
                  or "WIN" in (e.pattern_description or ""))
        losses = sum(1 for e in kb if "LOST" in (e.pattern_description or "")
                    or "LOSS" in (e.pattern_description or ""))
        results["knowledge_base"] = {"ok": len(kb) > 0, "total": len(kb),
                                     "win_patterns": wins, "loss_patterns": losses}
    except Exception as e:
        results["knowledge_base"] = {"ok": False, "error": str(e)[:50]}

    # 6. Auto-rules
    try:
        from src.memory.auto_rules import AutoRuleExtractor

        rules = AutoRuleExtractor.load()
        results["auto_rules"] = {
            "ok": True,
            "total": (len(rules.get("stock_vetoes", {})) + len(rules.get("stock_boosts", {}))
                     + len(rules.get("setup_vetoes", [])) + len(rules.get("setup_boosts", []))),
            "vetoes": len(rules.get("stock_vetoes", {})),
            "boosts": len(rules.get("stock_boosts", {})),
        }
    except Exception as e:
        results["auto_rules"] = {"ok": False, "error": str(e)[:50]}

    # 7. Drift detection
    try:
        from src.analytics.drift_detector import DriftDetector

        drift = DriftDetector().check_and_respond()
        results["drift"] = {"ok": not drift.get("any_drift", False),
                            "any_drift": drift.get("any_drift", False),
                            "wr_drift": drift.get("wr_drift", {}).get("detected", False)}
    except Exception as e:
        results["drift"] = {"ok": False, "error": str(e)[:50]}

    # 8. Kill switch
    results["kill_switch"] = {"ok": True, "active": Path("KILL_SWITCH").exists()}

    # 9. Continuous simulator progress
    try:
        from src.learning.continuous_simulator import ContinuousSimulator

        stats = ContinuousSimulator().get_stats()
        results["continuous_sim"] = {"ok": stats["total"] > 0, "total": stats["total"],
                                     "win_rate": stats["win_rate"],
                                     "stocks_covered": stats["stocks_covered"]}
    except Exception as e:
        results["continuous_sim"] = {"ok": False, "error": str(e)[:50]}

    return results


def format_telegram_report(results: dict) -> str:
    icons = {True: "✅", False: "❌"}
    lines = [
        "\U0001f3e5 *System Health Report*",
        f"_{datetime.now(IST).strftime('%d %b %Y %H:%M IST')}_",
        "─" * 24,
    ]

    def _fmt_rsi(r):
        rsi = r.get("rsi")
        return f"RSI={rsi:.1f}" if isinstance(rsi, (int, float)) else "RSI=?"

    checks = [
        ("Data Pipeline", "data_pipeline", lambda r: f"{r.get('rows', 0)} rows"),
        ("Indicators", "indicators", _fmt_rsi),
        ("Scout", "scout", lambda r: f"{r.get('candidates', 0)} candidates"),
        ("Pairs Trading", "pairs", lambda r: f"{r.get('opportunities', 0)} opps"),
        ("Knowledge Base", "knowledge_base", lambda r: f"{r.get('total', 0)} patterns"),
        ("Auto Rules", "auto_rules", lambda r: f"{r.get('total', 0)} rules"),
        ("Drift Check", "drift", lambda r: "DRIFT" if r.get("any_drift") else "stable"),
        ("Kill Switch", "kill_switch", lambda r: "ARMED" if r.get("active") else "ready"),
        ("Continuous Sim", "continuous_sim",
         lambda r: f"{r.get('total', 0)} sims ({r.get('win_rate', 0):.0f}% WR)"),
    ]

    all_ok = True
    for label, key, detail in checks:
        r = results.get(key, {})
        ok = r.get("ok", False)
        if not ok:
            all_ok = False
        icon = icons[ok]
        try:
            det = detail(r)
        except Exception:
            det = r.get("error", "—")
        lines.append(f"{icon} *{label}*: {det}")

    lines.append("─" * 24)
    if all_ok:
        lines.append("✅ All systems operational. Learning continuously.")
    else:
        failed = [k for k, r in results.items() if not r.get("ok", False)]
        lines.append(f"⚠️ Issues found: {', '.join(failed)}")
        lines.append("Check logs for details.")

    return "\n".join(lines)


if __name__ == "__main__":
    print("Running system health check...")
    health_results = check_all()
    report = format_telegram_report(health_results)
    print(report.replace("*", "").replace("_", ""))

    try:
        from src.notifications.telegram_bot import TelegramNotifier

        notifier = TelegramNotifier()
        if notifier.is_configured():
            notifier.send_message(report)
            print("Report sent to Telegram")
    except Exception as e:
        print(f"Telegram send failed: {e}")
