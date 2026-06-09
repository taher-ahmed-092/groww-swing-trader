"""
Trade journal backed by SQLite via SQLModel.

Records every proposed/executed/closed trade plus post-trade reflections. This is
the system's memory: lessons are retrieved here and fed back as context.

DB: data/journal/trades.db
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, Session, SQLModel, create_engine, select

from src.orchestrator.state import TradeState

_DB_DIR = os.path.join("data", "journal")
_DB_PATH = os.path.join(_DB_DIR, "trades.db")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TradeRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str
    sector: Optional[str] = None
    action: str = "BUY"
    signal: Optional[str] = None  # judge/technical signal at proposal (e.g. BUY)
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    confidence: Optional[float] = None
    judge_score: Optional[float] = None
    judge_flags: Optional[str] = None  # JSON-encoded list[str]
    broker_mode: str = "paper"
    proposed_at: Optional[datetime] = Field(default_factory=_now)
    executed_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    outcome: str = "OPEN"  # WIN / LOSS / OPEN
    reflection: Optional[str] = None


class TradingJournal:
    def __init__(self, db_path: str = _DB_PATH) -> None:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)
        SQLModel.metadata.create_all(self.engine)

    # ── writes ───────────────────────────────────────────────────────────────
    def log_proposed(self, state: TradeState) -> TradeRecord:
        tech = state.get("technical_verdict", {})
        judge = state.get("judge_verdict", {})
        record = TradeRecord(
            symbol=state.get("symbol", "UNKNOWN"),
            sector=state.get("sector") or None,
            action="BUY",
            signal=tech.get("signal"),
            entry_price=tech.get("entry_price"),
            stop_price=tech.get("stop_price"),
            target_price=tech.get("target_price"),
            confidence=tech.get("score"),
            judge_score=judge.get("score"),
            judge_flags=json.dumps(judge.get("flags", [])),
            outcome="OPEN",
        )
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    def log_executed(self, trade_id: int, result: dict) -> TradeRecord:
        with Session(self.engine) as session:
            record = session.get(TradeRecord, trade_id)
            if record is None:
                raise ValueError(f"No TradeRecord with id={trade_id}")
            record.executed_at = _now()
            record.broker_mode = result.get("broker_mode", record.broker_mode)
            if result.get("fill_price") is not None:
                record.entry_price = result["fill_price"]
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    def log_closed(self, trade_id: int, close_price: float) -> TradeRecord:
        with Session(self.engine) as session:
            record = session.get(TradeRecord, trade_id)
            if record is None:
                raise ValueError(f"No TradeRecord with id={trade_id}")
            entry = record.entry_price or 0.0
            qty_value = close_price - entry
            record.pnl = round(qty_value, 4)
            record.pnl_pct = round((qty_value / entry) * 100, 4) if entry else None
            record.outcome = "WIN" if qty_value > 0 else "LOSS"
            record.closed_at = _now()
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    def log_reflection(self, trade_id: int, reflection: str) -> TradeRecord:
        with Session(self.engine) as session:
            record = session.get(TradeRecord, trade_id)
            if record is None:
                raise ValueError(f"No TradeRecord with id={trade_id}")
            record.reflection = reflection
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    # ── reads ────────────────────────────────────────────────────────────────
    def get_recent(self, n: int = 10) -> list[TradeRecord]:
        with Session(self.engine) as session:
            stmt = select(TradeRecord).order_by(TradeRecord.id.desc()).limit(n)
            return list(session.exec(stmt).all())

    def get_lessons(self, symbol: Optional[str] = None) -> str:
        with Session(self.engine) as session:
            stmt = select(TradeRecord).where(TradeRecord.reflection.is_not(None))
            if symbol:
                stmt = stmt.where(TradeRecord.symbol == symbol)
            stmt = stmt.order_by(TradeRecord.id.desc()).limit(20)
            records = list(session.exec(stmt).all())

        if not records:
            return "No prior lessons recorded yet."
        return "\n".join(
            f"- [{r.symbol} {r.outcome}] {r.reflection}" for r in records if r.reflection
        )
