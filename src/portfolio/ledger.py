"""
Portfolio ledger backed by SQLite via SQLModel.

Tracks capital, deployment, realized P&L, and a compounding factor. Single-row
state table (one portfolio). Starting float: settings.PAPER_CAPITAL_INR (default ₹100,000).

DB: data/journal/portfolio.db
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, Session, SQLModel, create_engine, select

from config.settings import settings

_DB_DIR = os.path.join("data", "journal")
_DB_PATH = os.path.join(_DB_DIR, "portfolio.db")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _default_capital() -> float:
    # Paper/demo only — live capital always comes from the broker.
    return settings.paper_capital_inr


class PortfolioState(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    total_capital: float = Field(default_factory=_default_capital)
    deployed_capital: float = 0.0
    free_capital: float = Field(default_factory=_default_capital)
    closed_pnl: float = 0.0
    open_positions_count: int = 0
    compound_factor: float = 1.0
    last_updated: datetime = Field(default_factory=_now)


class PortfolioLedger:
    def __init__(self, db_path: str = _DB_PATH) -> None:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)
        SQLModel.metadata.create_all(self.engine)

    def _get_state(self, session: Session) -> Optional[PortfolioState]:
        return session.exec(select(PortfolioState).limit(1)).first()

    def initialize(self, starting_capital: float | None = None) -> PortfolioState:
        starting_capital = starting_capital if starting_capital is not None else settings.paper_capital_inr
        with Session(self.engine) as session:
            state = self._get_state(session)
            if state is None:
                state = PortfolioState(
                    total_capital=starting_capital,
                    free_capital=starting_capital,
                )
                session.add(state)
                session.commit()
                session.refresh(state)
            return state

    def get_summary(self) -> dict:
        with Session(self.engine) as session:
            state = self._get_state(session) or self.initialize()
            return {
                "total_capital": state.total_capital,
                "deployed_capital": state.deployed_capital,
                "free_capital": state.free_capital,
                "closed_pnl": state.closed_pnl,
                "open_positions_count": state.open_positions_count,
                "compound_factor": round(state.compound_factor, 4),
                "last_updated": state.last_updated.isoformat() if state.last_updated else None,
            }

    def record_trade_open(self, trade_id: int, value_inr: float) -> dict:
        with Session(self.engine) as session:
            state = self._get_state(session) or PortfolioState()
            state.deployed_capital += value_inr
            state.free_capital = state.total_capital - state.deployed_capital
            state.open_positions_count += 1
            state.last_updated = _now()
            session.add(state)
            session.commit()
            session.refresh(state)
        return self.get_summary()

    def record_trade_close(self, trade_id: int, pnl: float) -> dict:
        with Session(self.engine) as session:
            state = self._get_state(session) or PortfolioState()
            state.closed_pnl += pnl
            state.total_capital += pnl
            state.open_positions_count = max(0, state.open_positions_count - 1)
            state.free_capital = state.total_capital - state.deployed_capital
            base = state.total_capital - state.closed_pnl
            state.compound_factor = (state.total_capital / base) if base else 1.0
            state.last_updated = _now()
            session.add(state)
            session.commit()
            session.refresh(state)
        return self.get_summary()
