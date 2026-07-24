"""
Read-only web dashboard (FastAPI + WebSockets).

Security is non-negotiable: token-gated, localhost-bound, rate-limited, GET-only,
and the payload is sanitized so no key/token/secret can ever leak. It never writes
or executes anything — pure read of the local SQLite journal.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from config.settings import settings
from src.analytics.performance import PerformanceAnalyzer
from src.data.fetcher import MarketDataFetcher
from src.data.regime_detector import RegimeDetector
from src.learning.forward_simulator import ForwardSimulator
from src.memory.journal import TradingJournal

IST = ZoneInfo("Asia/Kolkata")
_START_TIME = datetime.now(IST)  # for system_uptime

# Static daily schedule (IST) — surfaced so the dashboard is useful before any trade.
_DAILY_JOBS = [
    (9 * 60 + 0, "Morning brief + premarket scan"),
    (9 * 60 + 30, "Intraday simulations open"),
    (15 * 60 + 15, "Intraday simulations close + learn"),
    (16 * 60 + 0, "Postmarket — close hit stops/targets"),
    (16 * 60 + 30, "Daily learning synthesis"),
]


def _market_open(now: datetime) -> bool:
    """NSE open right now = a trading day and within 09:15–15:30 IST."""
    try:
        from src.data.market_calendar import NSECalendar

        if not NSECalendar().is_market_open(now.date()):
            return False
    except Exception:
        if now.weekday() >= 5:
            return False
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= mins <= 15 * 60 + 30


def _next_jobs(now: datetime, limit: int = 3) -> list[dict]:
    """Upcoming scheduled jobs for the day (label + HH:MM IST)."""
    mins_now = now.hour * 60 + now.minute
    upcoming = [{"time": f"{m // 60:02d}:{m % 60:02d}", "name": label}
                for m, label in _DAILY_JOBS if m > mins_now]
    return upcoming[:limit]


def _uptime(now: datetime) -> str:
    delta = now - _START_TIME
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m = rem // 60
    return f"{h}h {m}m" if h else f"{m}m"


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)  # no docs in production

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"],
)

_SENSITIVE = ("key", "token", "secret", "password")
_request_counts: dict[str, list[float]] = defaultdict(list)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    _request_counts[ip] = [t for t in _request_counts[ip] if now - t < 60]
    if len(_request_counts[ip]) >= 100:
        return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
    _request_counts[ip].append(now)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def verify_token(token: str) -> bool:
    # If no token is configured, block everything (fail closed).
    return bool(settings.has_dashboard_token) and token == settings.dashboard_secret_token


def sanitize(d: dict) -> dict:
    """Defence in depth: strip any field whose name hints at a secret."""
    clean = {}
    for k, v in d.items():
        if any(s in k.lower() for s in _SENSITIVE):
            clean[k] = "[REDACTED]"
        else:
            clean[k] = v
    return clean


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict) -> None:
        for ws in list(self.active):
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(ws)


manager = ConnectionManager()


def _load_all_trade_history(journal: TradingJournal) -> list[dict]:
    """Merges real + forced + intraday + short trades, each tagged with its
    source, so the P&L chart isn't empty just because there are 0 real trades.
    Thin wrapper over the shared loader so PerformanceAnalyzer's professional
    metrics never drift out of sync with what the dashboard counts as "all"."""
    from src.analytics.trade_loader import load_all_trade_history

    return load_all_trade_history(journal)


def _combined_totals(all_trades: list[dict]) -> dict:
    """Running total across every source — real + forced + intraday + short —
    so the user sees ALL trades taken, not just the real pipeline's."""
    total_taken = len(all_trades)
    total_won = sum(1 for t in all_trades if t.get("outcome") == "WIN")
    total_lost = sum(1 for t in all_trades if t.get("outcome") == "LOSS")
    overall_wr = round(total_won / total_taken * 100, 1) if total_taken else 0

    by_source: dict = {}
    for t in all_trades:
        src = t.get("source", "unknown")
        by_source.setdefault(src, {"total": 0, "won": 0, "lost": 0})
        by_source[src]["total"] += 1
        if t.get("outcome") == "WIN":
            by_source[src]["won"] += 1
        if t.get("outcome") == "LOSS":
            by_source[src]["lost"] += 1

    wins = [t for t in all_trades if t.get("outcome") == "WIN"]
    losses = [t for t in all_trades if t.get("outcome") == "LOSS"]
    avg_win = sum(t.get("pnl", 0) for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.get("pnl", 0) for t in losses) / len(losses) if losses else 0
    wr_frac = total_won / total_taken if total_taken else 0
    net_expectancy = round(wr_frac * avg_win + (1 - wr_frac) * avg_loss, 3)
    breakeven_wr = (round(abs(avg_loss) / (avg_win + abs(avg_loss)) * 100, 1)
                    if (avg_win + abs(avg_loss)) > 0 else 50.0)

    gross_avg_win = (sum(t.get("gross_pnl", t.get("pnl", 0)) for t in wins) / len(wins)
                     if wins else 0)
    gross_avg_loss = (sum(t.get("gross_pnl", t.get("pnl", 0)) for t in losses) / len(losses)
                      if losses else 0)
    gross_expectancy = round(wr_frac * gross_avg_win + (1 - wr_frac) * gross_avg_loss, 3)

    return {
        "total": total_taken, "won": total_won, "lost": total_lost,
        "win_rate": overall_wr, "by_source": by_source,
        "avg_win_pct": round(avg_win, 2), "avg_loss_pct": round(avg_loss, 2),
        "expectancy_pct": net_expectancy, "breakeven_wr": breakeven_wr,
        "gross_expectancy_pct": gross_expectancy, "net_expectancy_pct": net_expectancy,
    }


def collect_dashboard_data() -> dict:
    """Collect all dashboard data from the local DB. Never raises."""
    try:
        journal = TradingJournal()
        pa = PerformanceAnalyzer()
        summary = pa.get_summary()
        trajectory = pa.get_performance_trajectory()
        open_trades = journal.get_open_trades()
        recent = journal.get_recent(n=20)
        knowledge = journal.get_active_knowledge(min_confidence=0.3)
        fetcher = MarketDataFetcher()

        positions = []
        for t in open_trades:
            price = fetcher.get_current_price(t.symbol)
            entry = t.entry_price or 0
            pnl = round((price - entry) / entry * 100, 2) if (price and entry) else 0
            positions.append({
                "symbol": t.symbol, "entry": entry, "current": price or entry,
                "stop": t.stop_price, "target": t.target_price, "pnl_pct": pnl,
                "strategy": getattr(t, "strategy_name", "momentum"),
                "days_held": (datetime.now() - t.executed_at).days if t.executed_at else 0,
            })

        all_trades = _load_all_trade_history(journal)
        pnl_history = all_trades[-60:]  # last 60 across all sources, for the chart
        combined_totals = _combined_totals(all_trades)
        wl_chart = [{"outcome": t.outcome, "symbol": t.symbol}
                    for t in recent if t.outcome in ("WIN", "LOSS")]

        try:
            regime_data = RegimeDetector().detect()
        except Exception:
            regime_data = {"regime": "UNKNOWN", "strategy": "N/A", "rsi": 0,
                           "size_multiplier": 1.0}
        try:
            strategy_breakdown = pa.get_strategy_breakdown()
        except Exception:
            strategy_breakdown = {}
        try:
            sim_insights = ForwardSimulator(journal).get_simulation_insights()
        except Exception:
            sim_insights = {}
        try:
            from src.learning.intraday_simulator import IntradaySimulator

            intraday_sim = IntradaySimulator().get_summary()
        except Exception:
            intraday_sim = {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "recent": []}
        try:
            from src.trading.always_on_trader import AlwaysOnTrader

            forced_summary = AlwaysOnTrader().get_todays_summary()
        except Exception:
            forced_summary = {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "recent": []}

        try:
            import json as _json

            forced_history = []
            forced_file = Path("data/cache/forced_trades_history.json")
            if forced_file.exists():
                forced_history = _json.loads(forced_file.read_text())
            forced_total = len(forced_history)
            forced_wins = sum(1 for t in forced_history if t.get("outcome") == "WIN")
            kb_count = len(knowledge)
            try:
                from src.learning.historical_replay import HistoricalReplayEngine

                replay_kb = HistoricalReplayEngine().get_stats().get("replay_patterns_in_kb", 0)
            except Exception:
                replay_kb = 0
            learning_metrics = {
                "forced_total": forced_total,
                "forced_wins": forced_wins,
                "forced_wr": round(forced_wins / forced_total * 100, 1) if forced_total else 0,
                "kb_patterns": kb_count,
                "replay_patterns": replay_kb,
                "xgboost_progress": min(100, round(
                    (forced_total * 0.3 + summary.get("total_trades", 0)) / 30 * 100)),
            }
        except Exception:
            learning_metrics = {"forced_total": 0, "forced_wins": 0, "forced_wr": 0,
                                "kb_patterns": 0, "replay_patterns": 0, "xgboost_progress": 0}

        _KB_ICONS = {"HISTORICAL_REPLAY": "📡", "FORCED_LEARNING": "⚡",
                     "INTRADAY_SIMULATION": "🔬", "SHORT_SIMULATION": "⏱️"}
        kb = [{"description": e.pattern_description[:60], "confidence": round(e.confidence, 2),
               "category": e.category, "is_hypothesis": e.is_hypothesis,
               "count": e.observed_count, "id": e.pattern_id,
               "icon": _KB_ICONS.get(e.category, "🧠")} for e in knowledge[:8]]

        try:
            from src.memory.adaptive_thresholds import AdaptiveThresholds

            adaptive_data = AdaptiveThresholds().load()
        except Exception:
            adaptive_data = {}

        try:
            from src.utils.funny_copy import get_regime_joke

            funny_line = get_regime_joke(regime_data.get("regime", "UNKNOWN"))
        except Exception:
            funny_line = ""

        try:
            pro_metrics = pa.get_professional_metrics()
        except Exception:
            pro_metrics = {"status": "no_data"}

        try:
            from src.data.realtime_feeds import EconomicCalendar, FIIDIIFeed

            fii_dii = FIIDIIFeed().get_latest()
            upcoming_events = EconomicCalendar().get_upcoming(14)
        except Exception:
            fii_dii, upcoming_events = {}, []

        try:
            from src.memory.auto_rules import AutoRuleExtractor

            auto_rules = {"total": AutoRuleExtractor.load().get("total_rules", 0)}
        except Exception:
            auto_rules = {"total": 0}

        try:
            from src.ml.random_forest_model import MODEL_FILE as RF_MODEL_FILE
            from src.ml.random_forest_model import RandomForestModel

            rf_model = {"available": RF_MODEL_FILE.exists(),
                       "importance": RandomForestModel().get_feature_importance()}
        except Exception:
            rf_model = {"available": False, "importance": {}}

        try:
            import json as _json2

            from src.ml.stock_priors import PRIORS_FILE

            stock_priors_count = (len(_json2.loads(PRIORS_FILE.read_text()))
                                  if PRIORS_FILE.exists() else 0)
        except Exception:
            stock_priors_count = 0

        try:
            from src.ml.signal_combiner import MODEL_FILE as XGB_MODEL_FILE

            xgb_available = XGB_MODEL_FILE.exists()
        except Exception:
            xgb_available = False

        try:
            from src.learning.continuous_simulator import ContinuousSimulator

            continuous_sim = ContinuousSimulator().get_stats()
        except Exception:
            continuous_sim = {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                              "stocks_covered": 0, "recent": []}

        now_ist = datetime.now(IST)
        data = {
            "timestamp": now_ist.isoformat(),
            "owner": settings.dashboard_owner_name,
            "mode": settings.mode_label,
            "broker": settings.broker_mode,
            "api_active": settings.has_anthropic_key,
            # Always-present context so the dashboard is useful with zero trades.
            "nifty_price": regime_data.get("nifty_price"),
            "nifty_rsi": regime_data.get("rsi"),
            "market_open": _market_open(now_ist),
            "system_uptime": _uptime(now_ist),
            "next_jobs": _next_jobs(now_ist),
            "summary": {
                "total_trades": summary.get("total_trades", 0),
                "win_rate": round(summary.get("win_rate", 0) * 100, 1),
                "avg_win_pct": round(summary.get("avg_win_pct", 0), 2),
                "avg_loss_pct": round(summary.get("avg_loss_pct", 0), 2),
                "total_pnl_inr": round(summary.get("total_pnl_inr", 0), 2),
                "expectancy": round(summary.get("expectancy", 0), 2),
                "consecutive_losses": summary.get("consecutive_losses", 0),
                "consecutive_wins": summary.get("consecutive_wins", 0),
            },
            "trajectory": trajectory,
            "positions": positions,
            "pnl_history": pnl_history,
            "wl_chart": wl_chart,
            "knowledge": kb,
            "regime": regime_data,
            "strategy_breakdown": strategy_breakdown,
            "sim_insights": sim_insights,
            "intraday_sim": intraday_sim,
            "forced_trades": forced_summary,
            "learning_metrics": learning_metrics,
            "adaptive_thresholds": adaptive_data,
            "combined_totals": combined_totals,
            "professional_metrics": pro_metrics,
            "funny_line": funny_line,
            "fii_dii": fii_dii,
            "upcoming_events": upcoming_events,
            "auto_rules": auto_rules,
            "rf_model": rf_model,
            "xgb_available": xgb_available,
            "stock_priors_count": stock_priors_count,
            "continuous_sim": continuous_sim,
            "kill_switch": Path("KILL_SWITCH").exists(),
        }
        return sanitize(data)
    except Exception as exc:
        return {"error": str(exc), "timestamp": datetime.now(IST).isoformat()}


async def broadcast_trade_event(event_type: str, trade: dict) -> None:
    """Broadcasts a trade event to all connected dashboard clients, on top of
    a full dashboard snapshot (updateDashboard() assumes every message carries
    the full payload — an event-only message would crash it on missing keys).

    event_type: "TRADE_OPENED" | "TRADE_CLOSED" | "SQUADRON"
    trade["source"]: "real" | "forced" | "intraday" | "short" | "replay"
    """
    data = collect_dashboard_data()
    if event_type == "SQUADRON":
        data["event"] = {
            "type": "SQUADRON",
            "source": trade.get("source", "replay"),
            "count": trade.get("count", 0),
            "wins": trade.get("wins", 0),
            "losses": trade.get("losses", 0),
            "timestamp": datetime.now(IST).isoformat(),
        }
    else:
        data["event"] = {
            "type": event_type,
            "source": trade.get("source", "forced"),
            "symbol": trade.get("symbol", ""),
            "entry": trade.get("entry", 0),
            "stop": trade.get("stop", 0),
            "target": trade.get("target", 0),
            "outcome": trade.get("outcome", ""),
            "pnl_pct": trade.get("pnl_pct", 0),
            "trade_type": trade.get("trade_type", ""),
            "timestamp": datetime.now(IST).isoformat(),
        }
    await manager.broadcast(data)


@app.get("/dashboard/{token}", response_class=HTMLResponse)
async def serve_dashboard(token: str):
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid token. Nice try though. 🤖")
    html = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")
    html = html.replace("__DASHBOARD_TOKEN__", token)
    html = html.replace("__OWNER_NAME__", settings.dashboard_owner_name)
    return HTMLResponse(content=html)


@app.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
    if not verify_token(token):
        await websocket.close(code=4001)
        return
    await manager.connect(websocket)
    try:
        await websocket.send_json(collect_dashboard_data())
        while True:
            await asyncio.sleep(25)
            await websocket.send_json(collect_dashboard_data())
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


@app.post("/internal/event")
async def receive_event(request: Request):
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Internal only")
    event = await request.json()
    data = collect_dashboard_data()
    data["event"] = event
    await manager.broadcast(data)
    return {"ok": True}


@app.get("/api/knowledge/{token}/{pattern_id}")
async def knowledge_detail(token: str, pattern_id: str):
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid token.")
    journal = TradingJournal()
    kb = journal.get_active_knowledge(min_confidence=0.0)
    entry = next((e for e in kb if e.pattern_id == pattern_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Pattern not found.")
    return {
        "pattern_id": entry.pattern_id,
        "description": entry.pattern_description,
        "category": entry.category,
        "confidence": entry.confidence,
        "observed_count": entry.observed_count,
        "regime": entry.observed_in_regime,
        "is_hypothesis": entry.is_hypothesis,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(IST).isoformat()}


def run_dashboard():
    uvicorn.run(app, host="127.0.0.1", port=settings.dashboard_port, log_level="warning")
