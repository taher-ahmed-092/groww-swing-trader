"""
Engine Scorecard — the missing meta-learning layer.

AdaptiveThresholds adapts at the pattern/regime level, but nothing previously
adjusted the system's own learning ENGINES when they lose money. This module
tracks per-engine profitability (LIVE_FORCED, HISTORICAL_SIM, CONTINUOUS_SIM,
SHORT_SIM) over their last 100 closed trades and auto-throttles or pauses an
engine that's consistently losing — the system adjusting its own behavior,
not just its pattern confidences.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger(__name__)

THROTTLE_FILE = Path("data/cache/engine_throttle.json")
ADAPTATION_LOG_FILE = Path("data/cache/adaptation_log.json")

MIN_TRADES_FOR_THROTTLE = 50
PF_PAUSE_THRESHOLD = 0.5
PF_THROTTLE_THRESHOLD = 0.8
PF_RESTORE_THRESHOLD = 1.2
# Below this many CURRENT-ERA trades, an engine is still proving itself post-fix
# — "probation" (half-size entries) lets evidence accumulate instead of either
# trading it silently at full size or leaving it stuck "paused" on dead
# pre-fix-era data forever.
PROBATION_MIN_TRADES = 20
# A "paused"/"throttled" state older than this is stale enough to re-evaluate
# immediately rather than wait for the scheduled apply_throttles() runs
# (4:30 PM / 11:15 PM) — otherwise a stale pre-fix "paused" state can deadlock
# an engine for hours: probation re-evaluation only happens inside
# apply_throttles(), so nothing lifts the pause until the next scheduled run.
STALE_THROTTLE_HOURS = 12

# HISTORICAL_SIM is retired on scorecard evidence — PF 0.31 pre-fix era, PF
# 0.21 post-fix, 62.6% noisy time-exits (audit finding). ContinuousSimulator
# covers the same off-hours learning need with a proper 3:1 R:R. Its history
# file stays for learning; always_on_trader.py no longer places new trades
# through it. Kept as a scorecard row (not deleted) so the retirement remains
# visible and auditable.
RETIRED_ENGINES = {"HISTORICAL_SIM"}
RETIREMENT_EVIDENCE = "PF 0.31 pre-fix era, PF 0.21 post-fix, 62.6% noisy time-exits"

# Each engine's history source + how to identify its trades within that file.
_ENGINE_SOURCES = {
    "LIVE_FORCED": ("data/cache/forced_trades_history.json",
                    lambda t: t.get("trade_type") in ("LIVE_FORCED", "RANDOM_FORCED")),
    "HISTORICAL_SIM": ("data/cache/forced_trades_history.json",
                       lambda t: t.get("trade_type") == "HISTORICAL_SIM"),
    "CONTINUOUS_SIM": ("data/cache/continuous_sim_history.json", lambda t: True),
    "SHORT_SIM": ("data/cache/short_trades_history.json", lambda t: True),
}

# REAL (actual paper-broker fills — TradingJournal, not a JSON cache) is
# handled separately from _ENGINE_SOURCES: it's real signal-quality evidence,
# never a disposable practice squad, so apply_throttles() must never write a
# throttle/pause/retire entry for it (see the explicit skip below).
REAL_ENGINE = "REAL"


def log_adaptation(entry_type: str, detail: str, evidence: str) -> None:
    """Shared adaptation-log writer — makes every self-adjustment visible,
    whatever adjusted it (thresholds, drift decay, or engine throttling)."""
    history = []
    if ADAPTATION_LOG_FILE.exists():
        try:
            history = json.loads(ADAPTATION_LOG_FILE.read_text())
        except Exception:
            pass
    history.append({
        "ts": datetime.now(IST).isoformat(),
        "type": entry_type,
        "detail": detail,
        "evidence": evidence,
    })
    try:
        ADAPTATION_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ADAPTATION_LOG_FILE.write_text(json.dumps(history[-200:]))
    except OSError:
        pass


class EngineScorecard:
    def compute(self) -> dict:
        """Per-engine {win_rate, avg_win, avg_loss, profit_factor, expectancy,
        n_trades} over each engine's last 100 CURRENT-ERA (post-f262968)
        closed trades — pre-fix trades came from a structurally broken engine
        (90-min holds vs 6%+ targets) and must not dilute the read on whether
        the fixed engine is actually working. Re-evaluates any stale
        paused/throttled state first (see reevaluate_if_stale) so callers
        never read a scorecard against a mode that's been deadlocked for
        hours waiting on the scheduled apply_throttles() job."""
        self.reevaluate_if_stale()
        return self._compute_raw()

    def _compute_raw(self) -> dict:
        from src.analytics.era import split_by_era

        scorecard: dict = {}
        for engine, (fname, matcher) in _ENGINE_SOURCES.items():
            trades = self._load_matching(fname, matcher)
            current_era, _all_time = split_by_era(trades)
            scorecard[engine] = self._score(current_era[-100:])
        scorecard[REAL_ENGINE] = self._score_real()
        return scorecard

    @staticmethod
    def _score_real() -> dict:
        """REAL reads live paper-broker fills from the journal, not a JSON
        cache — the actual pipeline's signal quality, scored the same way as
        the other engines (PF/WR/expectancy over the last 100 current-era
        CLOSED trades) plus n_open, since open positions carry live
        unrealized P&L but no realized pnl_pct yet."""
        from sqlmodel import Session, select

        from src.analytics.era import split_by_era
        from src.memory.journal import TradeRecord, TradingJournal

        journal = TradingJournal()
        with Session(journal.engine) as session:
            closed = list(session.exec(
                select(TradeRecord).where(TradeRecord.outcome != "OPEN")
                .order_by(TradeRecord.id.asc())
            ).all())
            n_open = len(list(session.exec(
                select(TradeRecord).where(TradeRecord.outcome == "OPEN")
            ).all()))

        closed_dicts = [
            {"outcome": t.outcome, "pnl_pct": t.pnl_pct or 0,
             "closed_at": t.closed_at.isoformat() if t.closed_at else None}
            for t in closed
        ]
        current_era, _all_time = split_by_era(closed_dicts)
        scored = EngineScorecard._score(current_era[-100:])
        scored["n_open"] = n_open
        return scored

    def reevaluate_if_stale(self) -> None:
        """If engine_throttle.json holds any "paused"/"throttled" state
        updated more than STALE_THROTTLE_HOURS ago, immediately re-run
        apply_throttles() — called from here (so any compute() caller, e.g.
        the dashboard, self-heals) and from runner.py's startup sequence (so a
        restart doesn't wait for the 4:30 PM / 11:15 PM scheduled jobs to lift
        a stale pre-fix pause)."""
        throttles = self._load_throttles()
        now = datetime.now(IST)
        for state in throttles.values():
            if state.get("mode") not in ("paused", "throttled"):
                continue
            try:
                updated = datetime.fromisoformat(state["updated"])
            except Exception:
                self.apply_throttles()
                return
            if (now - updated).total_seconds() > STALE_THROTTLE_HOURS * 3600:
                self.apply_throttles()
                return

    def apply_throttles(self) -> dict:
        """Writes engine_throttle.json based on _compute_raw() (current-era
        only); returns only the engines whose mode actually changed this run,
        logging each change. Below PROBATION_MIN_TRADES current-era trades, an
        engine goes to "probation" regardless of any stale pre-fix "paused"
        state — otherwise a correctly-fixed engine could stay paused forever
        on dead pre-fix data and never get the chance to prove itself with new
        evidence."""
        scorecard = self._compute_raw()
        current = self._load_throttles()
        changes: dict = {}

        for engine, stats in scorecard.items():
            if engine == REAL_ENGINE:
                # Real signal-quality evidence, not a disposable practice
                # engine — never throttled/paused/retired on early PF. No
                # throttle-file entry is written, so get_mode("REAL") always
                # reads back "normal" via its own default.
                continue

            n = stats["n_trades"]
            pf = stats["profit_factor"]
            prev_mode = current.get(engine, {}).get("mode", "normal")

            if engine in RETIRED_ENGINES:
                if prev_mode != "retired":
                    current[engine] = {
                        "mode": "retired", "pf": pf,
                        "updated": datetime.now(IST).isoformat(),
                        "reason": f"retired — {RETIREMENT_EVIDENCE}",
                    }
                    changes[engine] = {"old": prev_mode, "new": "retired", "pf": pf,
                                       "reason": RETIREMENT_EVIDENCE}
                    log_adaptation(
                        "retirement",
                        f"{engine}: retired ({RETIREMENT_EVIDENCE})",
                        f"PF={pf:.2f}, n_trades={n}, win_rate={stats['win_rate']:.0%}")
                continue

            # Bug fix: the old `elif n < MIN_TRADES_FOR_THROTTLE: new_mode =
            # prev_mode` branch meant an engine sitting in "probation" (the
            # normal case for n < 20) never got its PF evaluated at all
            # until n reached 50 — PROBATION_MIN_TRADES=20 was documented as
            # the probation exit point but the code silently required 50.
            # A PF clear of the pause/throttle/restore thresholds is a
            # strong-enough signal to act on at 20 trades already; only the
            # genuinely NEUTRAL band (between throttle and restore) still
            # waits for the fuller 50-trade sample before snapping to
            # "normal" on thin evidence.
            if n < PROBATION_MIN_TRADES:
                new_mode = "probation"
                reason = (f"current-era evidence still building ({n}/{PROBATION_MIN_TRADES} "
                          "trades) — half-size entries while proving itself")
            elif pf < PF_PAUSE_THRESHOLD:
                new_mode, reason = "paused", f"PF {pf:.2f} < {PF_PAUSE_THRESHOLD} over {n} trades"
            elif pf < PF_THROTTLE_THRESHOLD:
                new_mode, reason = "throttled", f"PF {pf:.2f} < {PF_THROTTLE_THRESHOLD} over {n} trades"
            elif pf > PF_RESTORE_THRESHOLD:
                new_mode, reason = "normal", f"PF {pf:.2f} > {PF_RESTORE_THRESHOLD} over {n} trades"
            elif n < MIN_TRADES_FOR_THROTTLE:
                new_mode = "probation" if prev_mode in ("probation", "paused") else prev_mode
                reason = f"neutral PF, still building evidence ({n}/{MIN_TRADES_FOR_THROTTLE} trades)"
            else:
                new_mode, reason = prev_mode, f"PF {pf:.2f} in neutral band over {n} trades"

            current[engine] = {
                "mode": new_mode, "pf": pf,
                "updated": datetime.now(IST).isoformat(), "reason": reason,
            }
            if new_mode != prev_mode:
                changes[engine] = {"old": prev_mode, "new": new_mode, "pf": pf, "reason": reason}
                log_adaptation(
                    "throttle",
                    f"{engine}: {prev_mode} -> {new_mode} ({reason})",
                    f"PF={pf:.2f}, n_trades={n}, win_rate={stats['win_rate']:.0%}")

        self._save_throttles(current)
        changes.update(self.apply_strategy_throttles())
        return changes

    # Strategy-level throttling — the honest version of "diversify across
    # styles": once a strategy has enough trades to trust its profit factor,
    # a chronically unprofitable one gets fewer future entries rather than
    # keeping full-size volume just because it's one of "the four styles."
    STRATEGY_THROTTLE_MIN_TRADES = 30
    STRATEGY_THROTTLE_PF_THRESHOLD = 0.8

    def apply_strategy_throttles(self) -> dict:
        """Halves entry frequency (returns {strategy: {frequency_multiplier}})
        for any strategy with >=30 trades and PF < 0.8, logging each new
        throttle to the adaptation log exactly once (idempotent via the
        throttle file's own strategy_ namespace)."""
        from src.analytics.strategy_attribution import compute_strategy_stats
        from src.analytics.trade_loader import load_all_trade_history
        from src.memory.journal import TradingJournal

        all_trades = load_all_trade_history(TradingJournal())
        stats = compute_strategy_stats(all_trades)
        current = self._load_throttles()
        changes: dict = {}

        for strategy, s in stats.items():
            key = f"strategy_{strategy}"
            n = s["trades"] or 0
            pf = s["profit_factor"]
            prev = current.get(key, {}).get("frequency_multiplier", 1.0)

            if n >= self.STRATEGY_THROTTLE_MIN_TRADES and pf is not None and pf < self.STRATEGY_THROTTLE_PF_THRESHOLD:
                new_mult = 0.5
            else:
                new_mult = 1.0

            if new_mult != prev:
                current[key] = {
                    "frequency_multiplier": new_mult, "pf": pf,
                    "updated": datetime.now(IST).isoformat(),
                    "reason": f"PF {pf} over {n} trades" if pf is not None else "insufficient evidence",
                }
                changes[strategy] = {"old": prev, "new": new_mult, "pf": pf}
                log_adaptation(
                    "strategy_throttle",
                    f"{strategy}: entry frequency x{prev} -> x{new_mult}",
                    f"PF={pf}, n_trades={n}")

        self._save_throttles(current)
        return changes

    @staticmethod
    def get_strategy_frequency_multiplier(strategy: str) -> float:
        """1.0 normal, 0.5 if throttled — read by entry points before deciding
        how many candidates of this strategy to place this cycle."""
        if not THROTTLE_FILE.exists():
            return 1.0
        try:
            data = json.loads(THROTTLE_FILE.read_text())
            return data.get(f"strategy_{strategy}", {}).get("frequency_multiplier", 1.0)
        except Exception:
            return 1.0

    @staticmethod
    def get_mode(engine: str) -> str:
        """normal | throttled | paused — read by entry points before trading."""
        if not THROTTLE_FILE.exists():
            return "normal"
        try:
            data = json.loads(THROTTLE_FILE.read_text())
            return data.get(engine, {}).get("mode", "normal")
        except Exception:
            return "normal"

    # ── internals ────────────────────────────────────────────────────────────
    @staticmethod
    def _load_matching(fname: str, matcher) -> list[dict]:
        p = Path(fname)
        if not p.exists():
            return []
        try:
            trades = json.loads(p.read_text())
        except Exception:
            return []
        return [t for t in trades if t.get("outcome") in ("WIN", "LOSS") and matcher(t)]

    @staticmethod
    def _score(trades: list[dict]) -> dict:
        n = len(trades)
        if n == 0:
            return {"win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                    "profit_factor": 0.0, "expectancy": 0.0, "n_trades": 0}
        pnls = [t.get("pnl_pct", 0) or 0 for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        win_rate = len(wins) / n
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        gross_wins = sum(wins)
        gross_losses = abs(sum(losses))
        profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else 999.99
        expectancy = round(win_rate * avg_win + (1 - win_rate) * avg_loss, 3)
        return {
            "win_rate": round(win_rate, 3), "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2), "profit_factor": profit_factor,
            "expectancy": expectancy, "n_trades": n,
        }

    @staticmethod
    def _load_throttles() -> dict:
        if THROTTLE_FILE.exists():
            try:
                return json.loads(THROTTLE_FILE.read_text())
            except Exception:
                pass
        return {}

    @staticmethod
    def _save_throttles(data: dict) -> None:
        try:
            THROTTLE_FILE.parent.mkdir(parents=True, exist_ok=True)
            THROTTLE_FILE.write_text(json.dumps(data, indent=2))
        except OSError:
            pass
