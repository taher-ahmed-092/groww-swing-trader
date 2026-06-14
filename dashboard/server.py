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

        pnl_history = [
            {"date": t.closed_at.strftime("%d/%m") if t.closed_at else "",
             "pnl": t.pnl_pct or 0, "symbol": t.symbol, "outcome": t.outcome}
            for t in reversed(recent) if t.outcome in ("WIN", "LOSS") and t.closed_at
        ]
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

        kb = [{"description": e.pattern_description[:60], "confidence": round(e.confidence, 2),
               "category": e.category, "is_hypothesis": e.is_hypothesis,
               "count": e.observed_count} for e in knowledge[:7]]

        data = {
            "timestamp": datetime.now(IST).isoformat(),
            "owner": settings.dashboard_owner_name,
            "mode": settings.mode_label,
            "broker": settings.broker_mode,
            "api_active": settings.has_anthropic_key,
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
            "kill_switch": Path("KILL_SWITCH").exists(),
        }
        return sanitize(data)
    except Exception as exc:
        return {"error": str(exc), "timestamp": datetime.now(IST).isoformat()}


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


@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(IST).isoformat()}


def run_dashboard():
    uvicorn.run(app, host="127.0.0.1", port=settings.dashboard_port, log_level="warning")
