"""Continuous simulator — window selection, no-lookahead, outcome detection,
and the statistical guard on knowledge-base confidence."""

from __future__ import annotations

import pandas as pd
import pytest

from src.learning.continuous_simulator import ContinuousSimulator


@pytest.fixture(autouse=True)
def _stub_regime_detector(monkeypatch):
    """run_batch() calls RegimeDetector().detect() once per batch to tag
    simulated trades with the prevailing market regime. Stub it so tests
    don't make a real network call."""
    from src.data.regime_detector import RegimeDetector

    monkeypatch.setattr(RegimeDetector, "detect", lambda self: {"regime": "NEUTRAL"})


def _make_df(n=120):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = [100 + i * 0.3 for i in range(n)]
    return pd.DataFrame(
        {
            "Open": close,
            "High": [c + 1 for c in close],
            "Low": [c - 1 for c in close],
            "Close": close,
            "Volume": [10000] * n,
        },
        index=idx,
    )


def test_pick_windows_no_overlap():
    sim = ContinuousSimulator.__new__(ContinuousSimulator)
    windows = sim._pick_windows(200, 3)
    assert len(windows) == len(set(windows))
    assert all(50 <= w <= 190 for w in windows)


def test_simulate_window_no_lookahead(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sim = ContinuousSimulator()
    df = _make_df(120)

    import src.learning.continuous_simulator as mod

    seen_lengths: list[int] = []
    original = mod.compute_indicators

    def spy(d):
        seen_lengths.append(len(d))
        return original(d)

    monkeypatch.setattr(mod, "compute_indicators", spy)
    sim._simulate_window("TESTCO", "large", df, 80)

    assert seen_lengths
    assert seen_lengths[0] == 81  # window_end + 1 rows only — no future data


def test_win_detection_stop_before_target():
    sim = ContinuousSimulator.__new__(ContinuousSimulator)
    df_after = pd.DataFrame(
        {
            "High": [101.0, 102.0, 103.0],
            "Low": [95.0, 90.0, 80.0],
            "Close": [98.0, 91.0, 82.0],
        }
    )
    outcome, exit_price, days = sim._check_outcome(entry=100.0, stop=90.0, target=130.0, df_after=df_after)
    assert outcome == "LOSS"
    assert exit_price == 90.0
    assert days == 2


def test_target_hit_before_stop():
    sim = ContinuousSimulator.__new__(ContinuousSimulator)
    df_after = pd.DataFrame(
        {
            "High": [101.0, 135.0, 103.0],
            "Low": [98.0, 99.0, 80.0],
            "Close": [100.0, 130.0, 82.0],
        }
    )
    outcome, exit_price, days = sim._check_outcome(entry=100.0, stop=90.0, target=130.0, df_after=df_after)
    assert outcome == "WIN"
    assert exit_price == 130.0
    assert days == 2


def test_run_batch_reports_counters_on_fetch_failure(tmp_path, monkeypatch):
    """A batch where every symbol fails to fetch must report fetch_failures
    (not just simulated=0) so the runner can distinguish 'quiet market' from
    'network is down' instead of both looking like silent no-ops.

    Since no symbol succeeds, backfill keeps pulling replacements until the
    entire (non-quarantined) watchlist has been attempted once — none reach
    the quarantine threshold within a single run, so every watchlist symbol
    is tried exactly once."""
    from src.data.watchlist import ALL_STOCKS

    monkeypatch.chdir(tmp_path)
    sim = ContinuousSimulator()
    monkeypatch.setattr(sim.fetcher, "get_price_history", lambda *a, **k: None)

    result = sim.run_batch()

    assert result["simulated"] == 0
    assert result["fetch_failures"] == len(ALL_STOCKS)
    assert result["windows_evaluated"] == 0
    assert result["skipped_no_signal"] == 0


def test_run_batch_reports_windows_evaluated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sim = ContinuousSimulator()
    df = _make_df(120)
    monkeypatch.setattr(sim.fetcher, "get_price_history", lambda *a, **k: df)

    result = sim.run_batch()

    assert result["windows_evaluated"] > 0
    assert result["fetch_failures"] == 0
    assert result["windows_evaluated"] == result["simulated"] + result["skipped_no_signal"]


def test_statistical_guard_low_initial_conf(tmp_path, monkeypatch):
    """A brand-new pattern must never start near 'proven' confidence — a
    single data point is never treated as proof."""
    monkeypatch.chdir(tmp_path)
    sim = ContinuousSimulator()
    ind = {"rsi_14": 55, "trend": "UPTREND", "adx_signal": "TRENDING"}
    added = sim._update_knowledge_safely("TESTCO", "large", ind, {"score": 0.5}, "WIN", "UPTREND")
    assert added is True

    kb = sim.journal.get_active_knowledge(min_confidence=0.0)
    assert len(kb) == 1
    assert kb[0].confidence <= 0.05


def test_quarantine_after_three_consecutive_failures_excludes_symbol(tmp_path, monkeypatch):
    """A symbol that fails to fetch three batches in a row (e.g. a delisted
    or renamed ticker) must be quarantined and excluded from subsequent
    batch selection, instead of permanently sorting to the front."""
    import src.learning.continuous_simulator as mod

    monkeypatch.chdir(tmp_path)
    small_watchlist = {f"SYM{i}": {"tier": "large"} for i in range(10)}
    monkeypatch.setattr(mod, "ALL_STOCKS", small_watchlist)

    sim = ContinuousSimulator()
    df = _make_df(120)
    monkeypatch.setattr(
        sim.fetcher,
        "get_price_history",
        lambda symbol, *a, **k: None if symbol == "SYM0" else df,
    )

    for _ in range(3):
        sim.run_batch()

    progress = sim._load_progress()
    assert progress["SYM0"]["consecutive_failures"] == 3
    assert "quarantined_until" in progress["SYM0"]

    result = sim.run_batch()
    assert "SYM0" not in result["batch_symbols"]


def test_run_batch_backfills_replacement_on_failure(tmp_path, monkeypatch):
    """When a symbol in the batch fails, the next overdue non-quarantined
    symbol must be pulled in as a replacement so BATCH_SIZE stocks are
    still actually evaluated."""
    import src.learning.continuous_simulator as mod

    monkeypatch.chdir(tmp_path)
    small_watchlist = {f"SYM{i}": {"tier": "large"} for i in range(10)}
    monkeypatch.setattr(mod, "ALL_STOCKS", small_watchlist)

    sim = ContinuousSimulator()
    df = _make_df(120)
    monkeypatch.setattr(
        sim.fetcher,
        "get_price_history",
        lambda symbol, *a, **k: None if symbol == "SYM0" else df,
    )

    result = sim.run_batch()

    assert result["fetch_failures"] == 1
    # 7 successes from the initial batch + the 1 failure + exactly 1
    # backfilled replacement to reach BATCH_SIZE successes.
    assert len(result["batch_symbols"]) == sim.BATCH_SIZE + 1


def test_get_stats_surfaces_quarantined_list(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sim = ContinuousSimulator()

    sim._save_progress(
        {
            "SYM0": {"last_sim": "2026-01-01", "consecutive_failures": 3, "quarantined_until": "2099-01-01"},
            "SYM1": {"last_sim": "2026-01-01", "consecutive_failures": 0},
        }
    )

    stats = sim.get_stats()

    assert stats["quarantined"] == {"SYM0": "2099-01-01"}


def test_confidence_decay_on_loss(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sim = ContinuousSimulator()
    ind = {"rsi_14": 55, "trend": "UPTREND", "adx_signal": "TRENDING"}

    sim._update_knowledge_safely("TESTCO", "large", ind, {"score": 0.5}, "WIN", "UPTREND")
    kb = sim.journal.get_active_knowledge(min_confidence=0.0)
    pattern_id = kb[0].pattern_id
    before = kb[0].confidence

    added = sim._update_knowledge_safely("OTHERCO", "large", ind, {"score": 0.5}, "LOSS", "UPTREND")
    assert added is False  # existing pattern updated, not a new one

    kb2 = sim.journal.get_active_knowledge(min_confidence=0.0)
    after = next(e for e in kb2 if e.pattern_id == pattern_id).confidence
    assert after < before
