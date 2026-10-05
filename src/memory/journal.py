"""
Trade journal backed by SQLite via SQLModel.

Records every proposed/executed/closed trade plus post-trade reflections. This is
the system's memory: lessons are retrieved here and fed back as context.

DB: data/journal/trades.db
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Field, Session, SQLModel, create_engine, select

from src.orchestrator.state import TradeState

_DB_DIR = os.path.join("data", "journal")
_DB_PATH = os.path.join(_DB_DIR, "trades.db")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Indicator field names as produced by src.agents.technical.indicators.compute_indicators
# (see ScoutAgent/HistoricalReplayEngine usage of the same dict). Shared across every
# entry-snapshot builder (real journal trades, forced/cont-sim/short simulations) so a
# post-mortem never has to guess which fields were even captured.
# Closes made by the startup staleness check after the process was down: the
# loss accrued over days offline, not today, so the daily-loss breaker ignores them.
CLOSE_REASON_STALE_CATCHUP = "STALE_CATCHUP"

ENTRY_SNAPSHOT_INDICATOR_KEYS = (
    "rsi_14", "adx_14", "adx_signal", "macd", "obv_trend", "supertrend_direction",
    "cmf_20", "ichimoku", "vwap_position", "atr_14", "ma_50", "ma_200",
    "candle_pattern", "week52_position",
)


def build_entry_snapshot(state: dict, tech: dict, judge: dict, fundamental: dict,
                          indicators: dict | None = None, scout_score: float | None = None,
                          extra_context: dict | None = None) -> dict:
    """Builds the nested "entry_snapshot" dict every trade record (real
    journal, forced, continuous-sim, short) should carry: indicators,
    market_context, scores, and an entry_reason string. Defensive throughout
    — a missing field is simply omitted, never an exception, since callers
    range from a fully-populated LangGraph TradeState to a bare simulation dict.
    `indicators` overrides any indicator fields found on `tech` (forced/cont-sim
    callers already have a raw compute_indicators() dict; real pipeline trades
    carry indicators nested inside technical_verdict instead)."""
    tech = tech or {}
    judge = judge or {}
    fundamental = fundamental or {}
    ind_source = {**tech, **(tech.get("indicators") or {}), **(indicators or {})}
    ind = {k: ind_source.get(k) for k in ENTRY_SNAPSHOT_INDICATOR_KEYS}

    ctx = dict(state.get("market_context", {}) or {}) if isinstance(state, dict) else {}
    ctx.update(extra_context or {})
    market_context = {
        "regime": ctx.get("regime"),
        "nifty_price": ctx.get("nifty_price"),
        "nifty_rsi": ctx.get("rsi", ctx.get("nifty_rsi")),
        "fii_signal": ctx.get("fii_signal", ctx.get("fii_trend")),
    }

    scores = {
        "scout": scout_score,
        "fundamental": fundamental.get("score"),
        "technical": tech.get("score"),
        "judge": judge.get("overall_score", judge.get("score")),
    }

    entry_reason = (tech.get("reasoning") or tech.get("rationale")
                    or judge.get("one_line_verdict") or "")

    return {"indicators": ind, "market_context": market_context,
            "scores": scores, "entry_reason": entry_reason}


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
    thread_id: Optional[str] = None  # LangGraph thread_id at proposal (for traceability)
    # Compact JSON of the entry-time verdicts — drives Root Cause Analysis reliably
    # without needing to reconstruct the full state from the checkpointer.
    state_snapshot: Optional[str] = None
    # Tiered-stop progression (updated as the trade is held).
    current_stop: Optional[float] = None
    partial_exited: bool = False
    partial_exit_price: Optional[float] = None
    partial_exit_pnl: Optional[float] = None
    # Rough API cost attributed to this trade's analysis (INR).
    api_cost_estimate_inr: Optional[float] = None
    # Seeded demo trades must NOT drive parameter auto-tuning (cold-start protection).
    is_seeded: bool = Field(default=False)
    # Which strategy produced this trade (momentum/mean_reversion/breakout/pairs_trading).
    strategy_name: str = Field(default="momentum")
    # Cost-model fields (src/trading/cost_model.py) — pnl/pnl_pct above are NET of
    # transaction costs; these preserve the gross figures for comparison.
    gross_pnl: Optional[float] = None
    gross_pnl_pct: Optional[float] = None
    # Realistic paper fill: signal_price is what the technical agent proposed,
    # fill_price includes tier-based slippage (src/trading/cost_model.py).
    signal_price: Optional[float] = None
    fill_price: Optional[float] = None
    # Why the position closed when it was NOT a normal monitored exit
    # (CLOSE_REASON_STALE_CATCHUP); None for ordinary closes.
    close_reason: Optional[str] = None


class DailyJournalEntry(SQLModel, table=True):
    """Level 2 — one synthesized entry per trading day."""
    id: Optional[int] = Field(default=None, primary_key=True)
    date: str = Field(index=True, unique=True)  # YYYY-MM-DD
    market_regime: str = "UNKNOWN"  # Nifty trend that day
    trades_proposed: int = 0
    trades_executed: int = 0
    trades_closed_today: int = 0
    wins_today: int = 0
    losses_today: int = 0
    total_pnl_today: float = 0.0
    patterns_observed: str = "[]"  # JSON list[str]
    synthesis: str = ""  # LLM daily summary, 150-200 words
    lessons_extracted: str = "[]"  # JSON list[str]
    created_at: datetime = Field(default_factory=_now)


class KnowledgeEntry(SQLModel, table=True):
    """Level 4 — a standing learned pattern with a confidence that compounds."""
    id: Optional[int] = Field(default=None, primary_key=True)
    pattern_id: str = Field(index=True)  # slug, e.g. "banking-overbought-fails"
    pattern_description: str = ""
    category: str = "MARKET_REGIME_PATTERN"
    confidence: float = 0.1  # 0.0-1.0
    observed_count: int = 1
    first_seen: datetime = Field(default_factory=_now)
    last_confirmed: datetime = Field(default_factory=_now)
    last_seen_in_trade: str = ""
    is_hypothesis: bool = True  # True while observed_count < 3
    is_active: bool = True
    supporting_trades: str = "[]"  # JSON list of trade ids
    observed_in_regime: str = Field(default="UNKNOWN")  # market regime when learned
    pattern_age_weight: float = Field(default=1.0)  # decays as the pattern ages


class SimulatedTrade(SQLModel, table=True):
    """24/7 paper-learning record: what would have happened to every candidate,
    traded or not. Outcomes are filled in 7/14 trading days later by the scheduler."""
    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str
    simulated_at: datetime = Field(default_factory=_now)
    signal: str = "SKIP"
    strategy_name: str = "unknown"
    regime: str = "UNKNOWN"
    entry_price: float = 0.0
    stop_price: float = 0.0
    target_price: float = 0.0
    fundamental_score: float = 0.0
    technical_score: float = 0.0
    judge_score: float = 0.0
    rejection_reason: str = ""
    outcome_7d: Optional[float] = None
    outcome_14d: Optional[float] = None
    would_have_won: Optional[bool] = None
    learned_from: bool = False


class APIUsageRecord(SQLModel, table=True):
    """Per-call LLM token usage for monthly cost estimation."""
    id: Optional[int] = Field(default=None, primary_key=True)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_inr_estimate: float = 0.0
    call_type: str = "REALTIME"  # REALTIME / BATCH / CACHED
    agent_name: str = ""
    created_at: datetime = Field(default_factory=_now)


class RootCauseRecord(SQLModel, table=True):
    """Forensic record of why a trade lost. Feeds weekly distillation."""
    id: Optional[int] = Field(default=None, primary_key=True)
    trade_id: int  # FK to TradeRecord.id
    symbol: str
    failure_category: str = "UNKNOWN"
    root_cause_analysis: str = ""
    what_signal_missed: str = ""
    actionable_lesson: str = ""
    created_at: datetime = Field(default_factory=_now)


# Threshold below which a pattern is still a hypothesis, not a rule.
HYPOTHESIS_MAX_COUNT = 3


class TradingJournal:
    def __init__(self, db_path: str = _DB_PATH) -> None:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)
        SQLModel.metadata.create_all(self.engine)
        self._migrate_new_columns()

    def _migrate_new_columns(self) -> None:
        """create_all() only creates missing TABLES, never adds columns to an
        existing one — an on-disk trades.db from before the cost-model fields
        were added would otherwise 500 on every query. Additive-only, nullable
        columns, so existing rows/history are never touched."""
        new_columns = {
            "gross_pnl": "FLOAT", "gross_pnl_pct": "FLOAT",
            "signal_price": "FLOAT", "fill_price": "FLOAT",
            "close_reason": "VARCHAR",
        }
        try:
            with self.engine.connect() as conn:
                existing = {row[1] for row in conn.exec_driver_sql(
                    "PRAGMA table_info(traderecord)").fetchall()}
                for col, coltype in new_columns.items():
                    if col not in existing:
                        conn.exec_driver_sql(
                            f"ALTER TABLE traderecord ADD COLUMN {col} {coltype}")
                conn.commit()
        except Exception:
            pass

    # ── writes ───────────────────────────────────────────────────────────────
    def log_proposed(self, state: TradeState) -> TradeRecord:
        tech = state.get("technical_verdict", {})
        judge = state.get("judge_verdict", {})
        fundamental = state.get("fundamental_verdict", {})
        # Compact, self-contained snapshot of the entry thesis for later RCA.
        snapshot = {
            "fundamental_verdict": fundamental,
            "technical_verdict": tech,
            "judge_verdict": judge,
            "market_context": state.get("market_context", {}),
            "sentiment": state.get("sentiment", {}),
            "sector": state.get("sector", ""),
            "manually_requested": bool(state.get("manually_requested")),
            "entry_snapshot": build_entry_snapshot(state, tech, judge, fundamental),
        }
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
            thread_id=state.get("thread_id"),
            state_snapshot=json.dumps(snapshot, default=str),
            current_stop=tech.get("stop_price"),
            strategy_name=tech.get("strategy_name") or state.get("strategy_name") or "momentum",
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

    def log_fill_prices(self, trade_id: int, signal_price: float,
                       fill_price: Optional[float]) -> TradeRecord:
        """Records the pre-slippage signal price alongside the actual paper fill
        price, so cost/expectancy analysis can see the slippage that was applied."""
        with Session(self.engine) as session:
            record = session.get(TradeRecord, trade_id)
            if record is None:
                raise ValueError(f"No TradeRecord with id={trade_id}")
            record.signal_price = signal_price
            record.fill_price = fill_price
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    def log_closed(self, trade_id: int, close_price: float,
                   close_reason: Optional[str] = None) -> TradeRecord:
        with Session(self.engine) as session:
            record = session.get(TradeRecord, trade_id)
            if record is None:
                raise ValueError(f"No TradeRecord with id={trade_id}")
            entry = record.entry_price or 0.0
            qty_value = close_price - entry
            gross_pnl_pct = round((qty_value / entry) * 100, 4) if entry else None
            record.gross_pnl = round(qty_value, 4)
            record.gross_pnl_pct = gross_pnl_pct
            record.pnl = round(qty_value, 4)
            record.pnl_pct = gross_pnl_pct
            if entry and gross_pnl_pct is not None:
                try:
                    from src.data.watchlist import ALL_STOCKS
                    from src.trading.cost_model import net_pnl_pct

                    tier = ALL_STOCKS.get(record.symbol, {}).get("tier", "large")
                    record.pnl_pct = net_pnl_pct(gross_pnl_pct, entry, close_price, tier,
                                                 is_intraday=False)
                    record.pnl = round(entry * record.pnl_pct / 100, 4)
                except Exception:
                    pass
            record.outcome = "WIN" if qty_value > 0 else "LOSS"
            record.close_reason = close_reason
            record.closed_at = _now()
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    def update_position(self, trade_id: int, *, current_stop: Optional[float] = None,
                        partial_exited: Optional[bool] = None,
                        partial_exit_price: Optional[float] = None,
                        partial_exit_pnl: Optional[float] = None) -> TradeRecord:
        """Update tiered-stop progression fields on a held position."""
        with Session(self.engine) as session:
            record = session.get(TradeRecord, trade_id)
            if record is None:
                raise ValueError(f"No TradeRecord with id={trade_id}")
            if current_stop is not None:
                record.current_stop = current_stop
            if partial_exited is not None:
                record.partial_exited = partial_exited
            if partial_exit_price is not None:
                record.partial_exit_price = partial_exit_price
            if partial_exit_pnl is not None:
                record.partial_exit_pnl = partial_exit_pnl
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

    # ── Level 2: daily journal ─────────────────────────────────────────────────
    def log_daily_entry(self, entry: DailyJournalEntry) -> DailyJournalEntry:
        with Session(self.engine) as session:
            # Upsert by date so re-running a day's synthesis overwrites, not duplicates.
            existing = session.exec(
                select(DailyJournalEntry).where(DailyJournalEntry.date == entry.date)
            ).first()
            if existing is not None:
                for field in (
                    "market_regime", "trades_proposed", "trades_executed",
                    "trades_closed_today", "wins_today", "losses_today",
                    "total_pnl_today", "patterns_observed", "synthesis", "lessons_extracted",
                ):
                    setattr(existing, field, getattr(entry, field))
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing
            session.add(entry)
            session.commit()
            session.refresh(entry)
        return entry

    def get_daily_entries(self, last_n_days: int = 7) -> list[DailyJournalEntry]:
        cutoff = (datetime.now(timezone.utc).date() - timedelta(days=last_n_days)).isoformat()
        with Session(self.engine) as session:
            stmt = (
                select(DailyJournalEntry)
                .where(DailyJournalEntry.date >= cutoff)
                .order_by(DailyJournalEntry.date.desc())
            )
            return list(session.exec(stmt).all())

    # ── API cost tracking ──────────────────────────────────────────────────────
    def log_api_usage(self, *, model: str, input_tokens: int, output_tokens: int,
                      call_type: str = "REALTIME", agent_name: str = "") -> None:
        # Rough Haiku-tier pricing in INR (₹84/$): ~$0.80/M in, ~$4/M out.
        # BATCH = 50% off; CACHED reads ~90% off the input portion.
        in_rate, out_rate = 0.80, 4.0
        if call_type == "BATCH":
            in_rate, out_rate = in_rate * 0.5, out_rate * 0.5
        elif call_type == "CACHED":
            in_rate *= 0.3  # blended estimate for mostly-cached prompts
        cost = ((input_tokens / 1_000_000) * in_rate
                + (output_tokens / 1_000_000) * out_rate) * 84.0
        with Session(self.engine) as session:
            session.add(APIUsageRecord(
                model=model, input_tokens=input_tokens, output_tokens=output_tokens,
                cost_inr_estimate=round(cost, 4), call_type=call_type, agent_name=agent_name))
            session.commit()

    def get_monthly_cost(self) -> float:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        with Session(self.engine) as session:
            rows = session.exec(select(APIUsageRecord)).all()
        total = 0.0
        for r in rows:
            ts = r.created_at
            if ts is not None and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts is None or ts >= cutoff:
                total += r.cost_inr_estimate or 0
        return round(total, 2)

    # ── Forward simulation (24/7 paper learning) ───────────────────────────────
    def log_simulated_trade(self, **kwargs) -> SimulatedTrade:
        sim = SimulatedTrade(**kwargs)
        with Session(self.engine) as session:
            session.add(sim)
            session.commit()
            session.refresh(sim)
        return sim

    def update_simulated_trade(self, sim: SimulatedTrade) -> SimulatedTrade:
        with Session(self.engine) as session:
            session.add(sim)
            session.commit()
            session.refresh(sim)
        return sim

    def get_simulations(self, limit: int = 500) -> list[SimulatedTrade]:
        with Session(self.engine) as session:
            stmt = select(SimulatedTrade).order_by(SimulatedTrade.id.desc()).limit(limit)
            return list(session.exec(stmt).all())

    def get_simulation_accuracy(self) -> dict:
        completed = [s for s in self.get_simulations() if s.would_have_won is not None]
        if not completed:
            return {"count": 0, "win_rate": None}
        wins = sum(1 for s in completed if s.would_have_won)
        return {"count": len(completed), "win_rate": round(wins / len(completed), 4)}

    # ── Root Cause Analysis records ────────────────────────────────────────────
    def log_rca(self, rca: RootCauseRecord) -> RootCauseRecord:
        with Session(self.engine) as session:
            session.add(rca)
            session.commit()
            session.refresh(rca)
        return rca

    def get_rcas(self, symbol: Optional[str] = None) -> list[RootCauseRecord]:
        with Session(self.engine) as session:
            stmt = select(RootCauseRecord)
            if symbol:
                stmt = stmt.where(RootCauseRecord.symbol == symbol)
            stmt = stmt.order_by(RootCauseRecord.id.desc())
            return list(session.exec(stmt).all())

    # ── Level 4: knowledge base ─────────────────────────────────────────────────
    def log_knowledge_entry(self, entry: KnowledgeEntry) -> KnowledgeEntry:
        with Session(self.engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
        return entry

    # Trading is never certain — 1.0 confidence is an overconfidence bug, not a
    # feature (it caused 13 patterns to reach "absolute certainty" and then
    # fail 71% of the time). Hard ceiling well below "certain"; floor keeps a
    # pattern alive as a weak hypothesis rather than being fully forgotten.
    MAX_CONFIDENCE = 0.90
    MIN_CONFIDENCE = 0.02

    def update_knowledge_confidence(
        self, pattern_id: str, confirmed: Optional[bool] = None,
        force_confidence: Optional[float] = None,
    ) -> Optional[KnowledgeEntry]:
        """confirmed=True/False: normal outcome-driven nudge (+0.1/-0.05).
        force_confidence: set confidence to an exact value instead — used by
        DriftDetector to decay all pattern confidences by a fixed factor when
        concept drift is detected. Pass confirmed=None with force_confidence
        set for a neutral decay (no observed_count/last_confirmed bump).
        Every path is clamped to [MIN_CONFIDENCE, MAX_CONFIDENCE] — no pattern
        can ever reach "absolute certainty" or be fully zeroed out."""
        with Session(self.engine) as session:
            entry = session.exec(
                select(KnowledgeEntry).where(KnowledgeEntry.pattern_id == pattern_id)
            ).first()
            if entry is None:
                return None
            if force_confidence is not None:
                new_confidence = force_confidence
                entry.observed_count += 1  # count as an update
            elif confirmed:
                new_confidence = entry.confidence + 0.1
                entry.observed_count += 1
                entry.last_confirmed = _now()
            else:
                new_confidence = entry.confidence - 0.05
            entry.confidence = round(
                max(self.MIN_CONFIDENCE, min(self.MAX_CONFIDENCE, new_confidence)), 4)
            entry.is_hypothesis = entry.observed_count < HYPOTHESIS_MAX_COUNT
            session.add(entry)
            session.commit()
            session.refresh(entry)
        return entry

    def get_active_knowledge(self, min_confidence: float = 0.0) -> list[KnowledgeEntry]:
        with Session(self.engine) as session:
            stmt = (
                select(KnowledgeEntry)
                .where(KnowledgeEntry.is_active == True)  # noqa: E712 (SQL boolean)
                .where(KnowledgeEntry.confidence >= min_confidence)
                .order_by(KnowledgeEntry.confidence.desc())
            )
            return list(session.exec(stmt).all())

    def update_pattern_ages(self) -> int:
        """Decay pattern_age_weight for patterns not confirmed in 30+ days. Weekly job."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        updated = 0
        with Session(self.engine) as session:
            for e in session.exec(select(KnowledgeEntry)).all():
                lc = e.last_confirmed
                if lc is not None and lc.tzinfo is None:
                    lc = lc.replace(tzinfo=timezone.utc)
                if lc is not None and lc < cutoff and e.pattern_age_weight > 0.1:
                    e.pattern_age_weight = round(e.pattern_age_weight * 0.9, 4)
                    session.add(e)
                    updated += 1
            session.commit()
        return updated

    def format_knowledge_for_context(
        self, min_confidence: float = 0.3, current_regime: str | None = None
    ) -> str:
        entries = [
            e for e in self.get_active_knowledge(min_confidence=0.0)
            if e.confidence > min_confidence
        ]
        if not entries:
            return ""

        def weight(e) -> float:
            # Confidence × age decay × regime relevance (2× same-regime, 0.5× other).
            regime_w = 1.0
            if current_regime and e.observed_in_regime not in ("UNKNOWN", ""):
                regime_w = 2.0 if e.observed_in_regime == current_regime else 0.5
            return e.confidence * (e.pattern_age_weight or 1.0) * regime_w

        entries.sort(key=weight, reverse=True)

        def bucket(c: float) -> str:
            if c >= 0.7:
                return "HIGH"
            if c >= 0.4:
                return "MED "
            return "LOW "

        lines = ["SYSTEM LEARNED PATTERNS (confidence-ranked):"]
        for e in entries:
            tag = " (hypothesis)" if e.is_hypothesis else ""
            lines.append(
                f"[{bucket(e.confidence)} {e.confidence:.2f}] {e.pattern_description} "
                f"— seen {e.observed_count}x{tag}"
            )
        return "\n".join(lines)

    def get_performance_summary(self) -> dict:
        """Aggregate stats over closed trades (drives Kelly sizing & dashboards)."""
        with Session(self.engine) as session:
            stmt = (
                select(TradeRecord)
                .where(TradeRecord.outcome != "OPEN")
                .order_by(TradeRecord.id.asc())
            )
            closed = list(session.exec(stmt).all())

        total = len(closed)
        if total == 0:
            return {
                "total_closed": 0, "win_rate": 0.0, "avg_win_pct": 0.0,
                "avg_loss_pct": 0.0, "consecutive_wins": 0, "consecutive_losses": 0,
            }

        wins = [t for t in closed if t.outcome == "WIN"]
        losses = [t for t in closed if t.outcome == "LOSS"]

        def _avg(rows):
            vals = [r.pnl_pct for r in rows if r.pnl_pct is not None]
            return round(sum(vals) / len(vals), 4) if vals else 0.0

        # Current streaks (walk back from most recent).
        cons_wins = cons_losses = 0
        for t in reversed(closed):
            if t.outcome == "WIN" and cons_losses == 0:
                cons_wins += 1
            elif t.outcome == "LOSS" and cons_wins == 0:
                cons_losses += 1
            else:
                break

        return {
            "total_closed": total,
            "win_rate": round(len(wins) / total, 4),
            "avg_win_pct": _avg(wins),
            "avg_loss_pct": _avg(losses),
            "consecutive_wins": cons_wins,
            "consecutive_losses": cons_losses,
        }

    def get_open_trades(self) -> list[TradeRecord]:
        with Session(self.engine) as session:
            return list(session.exec(
                select(TradeRecord).where(TradeRecord.outcome == "OPEN")
            ).all())

    def get_closed_trades(self) -> list[TradeRecord]:
        with Session(self.engine) as session:
            return list(session.exec(
                select(TradeRecord).where(TradeRecord.outcome != "OPEN")
            ).all())

    def get_strategy_win_rate(self, strategy_name: str, regime: Optional[str] = None) -> Optional[float]:
        """Win rate for a strategy (optionally within a regime). None if < 3 trades."""
        with Session(self.engine) as session:
            stmt = (
                select(TradeRecord)
                .where(TradeRecord.strategy_name == strategy_name)
                .where(TradeRecord.outcome != "OPEN")
            )
            trades = list(session.exec(stmt).all())

        if regime:
            filtered = []
            for t in trades:
                try:
                    snap = json.loads(t.state_snapshot or "{}")
                    if snap.get("market_context", {}).get("regime") == regime:
                        filtered.append(t)
                except (json.JSONDecodeError, TypeError):
                    continue
            trades = filtered

        if len(trades) < 3:
            return None
        wins = sum(1 for t in trades if t.outcome == "WIN")
        return round(wins / len(trades), 4)

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
