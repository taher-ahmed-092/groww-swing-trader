"""Trade pop-up WebSocket broadcast — no crash with zero connected clients."""
from __future__ import annotations

from dashboard.server import broadcast_trade_event


async def test_broadcast_trade_event_no_crash():
    trade = {"symbol": "TEST", "entry": 100.0, "stop": 95.0, "target": 110.0,
             "outcome": "WIN", "pnl_pct": 5.0, "trade_type": "LIVE_FORCED"}
    await broadcast_trade_event("TRADE_OPENED", trade)
    await broadcast_trade_event("TRADE_CLOSED", trade)
