"""Two runner.py fixes:

1. _run_guarantee's "simulated" notify fired every 30 min for the same
   symbol on a day with no qualifying setup (intraday_scan_job retries
   often) — 12+ identical Telegram messages. _sim_log_already_sent dedupes
   per (symbol, IST calendar day) via data/cache/sim_log_sent_today.json.
2. continuous_sim_job used to lump a full-batch fetch failure (rate limit /
   network blip hitting every symbol at once) into the same per-symbol
   warning as a handful of individual failures. When fetch_failures equals
   the number of symbols attempted, it's not those symbols' fault — log a
   distinct FULL BATCH FAILURE message and skip the next scheduled run once.
"""
from __future__ import annotations

import json

import runner


def test_sim_log_dedupes_same_symbol_same_day(tmp_path, monkeypatch):
    cache_file = tmp_path / "sim_log_sent_today.json"
    monkeypatch.setattr(runner, "_SIM_LOG_SENT_FILE", cache_file)

    assert runner._sim_log_already_sent("BANDHANBNK") is False
    assert runner._sim_log_already_sent("BANDHANBNK") is True
    assert runner._sim_log_already_sent("BANDHANBNK") is True


def test_sim_log_allows_different_symbols_same_day(tmp_path, monkeypatch):
    cache_file = tmp_path / "sim_log_sent_today.json"
    monkeypatch.setattr(runner, "_SIM_LOG_SENT_FILE", cache_file)

    assert runner._sim_log_already_sent("BANDHANBNK") is False
    assert runner._sim_log_already_sent("RELIANCE") is False
    assert runner._sim_log_already_sent("BANDHANBNK") is True


def test_sim_log_resets_on_new_day(tmp_path, monkeypatch):
    cache_file = tmp_path / "sim_log_sent_today.json"
    cache_file.write_text(json.dumps({"date": "2000-01-01", "symbols": ["BANDHANBNK"]}))
    monkeypatch.setattr(runner, "_SIM_LOG_SENT_FILE", cache_file)

    assert runner._sim_log_already_sent("BANDHANBNK") is False


def test_full_batch_fetch_failure_gets_distinct_log_and_skips_next_run(monkeypatch, capsys):
    monkeypatch.setattr(runner, "_kill_switch", lambda: False)
    monkeypatch.setattr(runner, "_is_scan_running", lambda: False)
    monkeypatch.setattr(runner, "_CONT_SIM_BACKOFF_UNTIL", 0.0)

    class _FakeSim:
        def run_batch(self):
            return {
                "simulated": 0, "wins": 0, "losses": 0, "new_patterns": 0,
                "windows_evaluated": 0, "skipped_no_signal": 0,
                "fetch_failures": 8, "batch_symbols": list(range(8)),
            }

    import src.learning.continuous_simulator as cs_module
    monkeypatch.setattr(cs_module, "ContinuousSimulator", _FakeSim)

    runner.continuous_sim_job()
    out = capsys.readouterr().out
    assert "FULL BATCH FAILURE" in out
    assert runner._CONT_SIM_BACKOFF_UNTIL > 0.0

    # Next scheduled run within the backoff window is skipped once, then cleared.
    runner.continuous_sim_job()
    out2 = capsys.readouterr().out
    assert "backing off after full-batch failure" in out2
    assert runner._CONT_SIM_BACKOFF_UNTIL == 0.0


def test_partial_fetch_failures_still_use_per_symbol_warning(monkeypatch, capsys):
    monkeypatch.setattr(runner, "_kill_switch", lambda: False)
    monkeypatch.setattr(runner, "_is_scan_running", lambda: False)
    monkeypatch.setattr(runner, "_CONT_SIM_BACKOFF_UNTIL", 0.0)

    class _FakeSim:
        def run_batch(self):
            return {
                "simulated": 0, "wins": 0, "losses": 0, "new_patterns": 0,
                "windows_evaluated": 0, "skipped_no_signal": 0,
                "fetch_failures": 6, "batch_symbols": list(range(8)),
            }

    import src.learning.continuous_simulator as cs_module
    monkeypatch.setattr(cs_module, "ContinuousSimulator", _FakeSim)

    runner.continuous_sim_job()
    out = capsys.readouterr().out
    assert "FULL BATCH FAILURE" not in out
    assert "fetch failures" in out
    assert runner._CONT_SIM_BACKOFF_UNTIL == 0.0
