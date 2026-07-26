"""Shared trade-history loader — every source must be represented, not just
the weakest engine (audit finding: continuous_sim_history.json was omitted,
so the headline scoreboard hid the best-performing engine)."""
from __future__ import annotations

import json

from src.analytics.trade_loader import load_all_trade_history
from src.memory.journal import TradingJournal


def test_includes_continuous_sim_trades(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "cache").mkdir(parents=True)
    (tmp_path / "data" / "cache" / "continuous_sim_history.json").write_text(json.dumps([
        {"symbol": "TCS", "pnl_pct": 4.2, "outcome": "WIN",
         "simulated_at": "2026-01-01T10:00:00+05:30", "entry": 100.0, "stop": 95.0},
        {"symbol": "INFY", "pnl_pct": -1.5, "outcome": "LOSS",
         "simulated_at": "2026-01-02T10:00:00+05:30", "entry": 200.0, "stop": 190.0},
    ]))
    journal = TradingJournal(db_path=str(tmp_path / "j.db"))

    merged = load_all_trade_history(journal)

    cont_sim_rows = [t for t in merged if t["source"] == "cont_sim"]
    assert len(cont_sim_rows) == 2
    assert {t["symbol"] for t in cont_sim_rows} == {"TCS", "INFY"}
    assert cont_sim_rows[0]["closed_at"]  # falls back to simulated_at, not blank


def test_missing_continuous_sim_file_is_fine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    journal = TradingJournal(db_path=str(tmp_path / "j.db"))
    merged = load_all_trade_history(journal)
    assert merged == []
