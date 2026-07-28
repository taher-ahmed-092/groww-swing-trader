"""
Company Dossier — persistent per-stock memory.

One JSON file per stock at data/dossiers/{SYMBOL}.json, accumulating
fundamentals snapshots, technical snapshots, and trade history across every
engine (real pipeline, forced learning, continuous sim, intraday sim). Lets
the system reason about a specific stock's own track record ("this stock is
2W/7L for us historically") rather than only generic patterns.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_DOSSIER_DIR = Path("data/dossiers")
_MAX_HISTORY = 100


def _now_iso() -> str:
    return datetime.now(IST).isoformat()


def _empty_dossier(symbol: str, sector: str | None = None, tier: str | None = None) -> dict:
    return {
        "symbol": symbol,
        "sector": sector,
        "tier": tier,
        "fundamentals_history": [],
        "technical_snapshots": [],
        "trade_history": [],
        "aggregates": {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "avg_pnl_pct": 0.0, "best_regime": None, "worst_regime": None,
            "avg_hold_days": 0.0, "bayesian_win_prob": 0.5,
        },
        "notes": [],
        "last_updated": _now_iso(),
    }


class DossierStore:
    def __init__(self, base_dir: Path | str = _DOSSIER_DIR) -> None:
        self.base_dir = Path(base_dir)

    def _path(self, symbol: str) -> Path:
        return self.base_dir / f"{symbol.upper()}.json"

    def get(self, symbol: str) -> dict:
        path = self._path(symbol)
        if not path.exists():
            return _empty_dossier(symbol.upper())
        try:
            return json.loads(path.read_text())
        except Exception:
            return _empty_dossier(symbol.upper())

    def _save(self, symbol: str, dossier: dict) -> None:
        dossier["last_updated"] = _now_iso()
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            self._path(symbol).write_text(json.dumps(dossier, indent=2, default=str))
        except OSError:
            pass

    def record_fundamentals(self, symbol: str, data: dict) -> None:
        """Appends a fundamentals snapshot whenever screener/yfinance data is
        successfully fetched for this symbol."""
        dossier = self.get(symbol)
        if data.get("sector"):
            dossier["sector"] = data["sector"]
        entry = {
            "date": _now_iso()[:10],
            "roce_pct": data.get("roce_pct"),
            "roe_pct": data.get("roe_pct"),
            "de_ratio": data.get("debt_to_equity"),
            "promoter_holding_pct": data.get("promoter_holding_pct"),
            "promoter_pledged_pct": data.get("promoter_pledged_pct"),
            "piotroski_score": data.get("piotroski_score"),
            "pe_ratio": data.get("pe_ratio"),
            "source": data.get("source", "screener"),
        }
        dossier["fundamentals_history"].append(entry)
        dossier["fundamentals_history"] = dossier["fundamentals_history"][-_MAX_HISTORY:]
        self._save(symbol, dossier)

    def record_technical(self, symbol: str, indicators: dict) -> None:
        """Appends a technical snapshot at trade-entry time."""
        dossier = self.get(symbol)
        entry = {
            "date": _now_iso()[:10],
            "rsi_14": indicators.get("rsi_14"),
            "adx_14": indicators.get("adx_14"),
            "adx_signal": indicators.get("adx_signal"),
            "trend": indicators.get("trend"),
            "supertrend_direction": indicators.get("supertrend_direction"),
            "obv_trend": indicators.get("obv_trend"),
            "cmf_20": indicators.get("cmf_20"),
            "week52_position": indicators.get("week52_position"),
            "atr_pct": indicators.get("atr_pct"),
            "close": indicators.get("close"),
        }
        dossier["technical_snapshots"].append(entry)
        dossier["technical_snapshots"] = dossier["technical_snapshots"][-_MAX_HISTORY:]
        self._save(symbol, dossier)

    def record_trade(self, symbol: str, trade: dict) -> None:
        """Appends a trade (open or closed) and recomputes aggregates.
        Only closed (WIN/LOSS) trades count toward aggregates."""
        dossier = self.get(symbol)
        if trade.get("tier"):
            dossier["tier"] = trade["tier"]
        entry = {
            "date": (trade.get("closed_at") or trade.get("opened_at") or _now_iso())[:10],
            "source": trade.get("source") or trade.get("trade_type", "unknown"),
            "strategy": trade.get("strategy"),
            "entry": trade.get("entry") or trade.get("entry_price"),
            "stop": trade.get("stop") or trade.get("stop_price"),
            "target": trade.get("target") or trade.get("target_price"),
            "exit": trade.get("exit") or trade.get("exit_price"),
            "pnl_pct": trade.get("pnl_pct"),
            "outcome": trade.get("outcome"),
            "hold_days": trade.get("days_held") or trade.get("hold_days"),
            "loss_reason": trade.get("loss_reason"),
            "market_regime": trade.get("market_regime") or trade.get("regime"),
        }
        dossier["trade_history"].append(entry)
        dossier["trade_history"] = dossier["trade_history"][-_MAX_HISTORY:]
        self._recompute_aggregates(dossier)
        self._save(symbol, dossier)

    def _recompute_aggregates(self, dossier: dict) -> None:
        closed = [t for t in dossier["trade_history"] if t.get("outcome") in ("WIN", "LOSS")]
        total = len(closed)
        wins = sum(1 for t in closed if t["outcome"] == "WIN")
        losses = total - wins
        pnls = [t.get("pnl_pct") or 0 for t in closed]
        avg_pnl = round(sum(pnls) / total, 3) if total else 0.0
        hold_days = [t.get("hold_days") for t in closed if t.get("hold_days") is not None]
        avg_hold = round(sum(hold_days) / len(hold_days), 2) if hold_days else 0.0

        by_regime: dict[str, list[float]] = {}
        for t in closed:
            regime = t.get("market_regime") or "UNKNOWN"
            by_regime.setdefault(regime, []).append(t.get("pnl_pct") or 0)
        best_regime = worst_regime = None
        if by_regime:
            regime_avgs = {r: sum(v) / len(v) for r, v in by_regime.items()}
            best_regime = max(regime_avgs, key=regime_avgs.get)
            worst_regime = min(regime_avgs, key=regime_avgs.get)

        # Simple Bayesian win probability: Beta(1,1) prior + observed wins/losses.
        bayesian_win_prob = round((wins + 1) / (total + 2), 4) if total else 0.5

        dossier["aggregates"] = {
            "total_trades": total, "wins": wins, "losses": losses,
            "win_rate": round(wins / total * 100, 1) if total else 0.0,
            "avg_pnl_pct": avg_pnl, "best_regime": best_regime, "worst_regime": worst_regime,
            "avg_hold_days": avg_hold, "bayesian_win_prob": bayesian_win_prob,
        }

    def get_aggregates(self, symbol: str) -> dict:
        return self.get(symbol).get("aggregates", {})

    def add_note(self, symbol: str, note: str) -> None:
        dossier = self.get(symbol)
        dossier["notes"].append(f"{_now_iso()[:10]}: {note}")
        dossier["notes"] = dossier["notes"][-_MAX_HISTORY:]
        self._save(symbol, dossier)

    def search(self, sector: str | None = None, min_win_rate: float | None = None) -> list[dict]:
        """Scans all persisted dossiers, filtering by sector and/or minimum
        win rate (aggregates.win_rate, 0-100 scale)."""
        results = []
        if not self.base_dir.exists():
            return results
        for path in self.base_dir.glob("*.json"):
            try:
                dossier = json.loads(path.read_text())
            except Exception:
                continue
            if sector and (dossier.get("sector") or "").lower() != sector.lower():
                continue
            if min_win_rate is not None and dossier.get("aggregates", {}).get("win_rate", 0) < min_win_rate:
                continue
            results.append(dossier)
        return results

    def summarize_for_prompt(self, symbol: str) -> str:
        """Short human-readable summary block for LLM prompts / pattern-matcher
        rationale strings — e.g. 'this stock is 2W/7L for us historically'."""
        dossier = self.get(symbol)
        agg = dossier.get("aggregates", {})
        total = agg.get("total_trades", 0)
        if total == 0:
            return f"{symbol}: no trade history yet."
        parts = [
            f"{symbol}: {agg.get('wins', 0)}W/{agg.get('losses', 0)}L "
            f"({agg.get('win_rate', 0):.0f}% WR, {total} trades, "
            f"avg {agg.get('avg_pnl_pct', 0):+.1f}%/trade)"
        ]
        if agg.get("best_regime"):
            parts.append(f"best in {agg['best_regime']}, worst in {agg.get('worst_regime')}")
        fh = dossier.get("fundamentals_history") or []
        if fh:
            latest = fh[-1]
            parts.append(
                f"latest fundamentals: ROCE {latest.get('roce_pct')}%, "
                f"ROE {latest.get('roe_pct')}%, D/E {latest.get('de_ratio')}"
            )
        return " | ".join(parts)


# Module-level singleton for convenient reuse across call sites.
dossier_store = DossierStore()
