"""Concept drift detector — win-rate drift, regime drift, and the confidence
decay response, all isolated to a tmp_path cache dir + fresh journal DB."""
from __future__ import annotations

import json
from pathlib import Path

from src.analytics.drift_detector import WR_DRIFT_WINDOW, DriftDetector
from src.memory.journal import KnowledgeEntry


def _write_sims(n_win_first_half, n_win_second_half, regime="VOLATILE",
                 market_regime=None, tag_market_regime=True):
    """WR_DRIFT_WINDOW sims in each half; win/loss ratio per half controlled
    by caller. Win counts are expressed out of 30 (historical test-authoring
    convention) and scaled to the current window size (100 post Fix 4 — a
    30-trade window swung 47pp on ordinary variance).

    `market_regime` defaults to `regime` (both fields describing the same
    market-wide category in these tests). Pass `tag_market_regime=False` to
    simulate legacy records that predate the `market_regime` field."""
    if market_regime is None:
        market_regime = regime
    n = WR_DRIFT_WINDOW
    wins_first = round(n_win_first_half / 30 * n)
    wins_second = round(n_win_second_half / 30 * n)
    trades = []
    for i in range(n):
        outcome = "WIN" if i < wins_first else "LOSS"
        row = {"outcome": outcome, "regime": regime, "rsi": 52,
               "simulated_at": f"2026-01-{i + 1:03d}T00:00:00"}
        if tag_market_regime:
            row["market_regime"] = market_regime
        trades.append(row)
    for i in range(n):
        outcome = "WIN" if i < wins_second else "LOSS"
        row = {"outcome": outcome, "regime": regime, "rsi": 52,
               "simulated_at": f"2026-02-{i + 1:03d}T00:00:00"}
        if tag_market_regime:
            row["market_regime"] = market_regime
        trades.append(row)
    p = Path("data/cache/forced_trades_history.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(trades))


def test_no_drift_stable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sims(n_win_first_half=15, n_win_second_half=15)  # 50% both halves
    result = DriftDetector()._check_wr_drift()
    assert result["detected"] is False


def test_wr_drift_detected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Previous half: 20/30 wins (67%). Recent half: 5/30 wins (17%) — 50pp drop.
    _write_sims(n_win_first_half=20, n_win_second_half=5)
    result = DriftDetector()._check_wr_drift()
    assert result["detected"] is True
    assert result["drop"] >= 15


def test_regime_drift_detected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sims(n_win_first_half=15, n_win_second_half=15, regime="VOLATILE")

    import src.data.regime_detector as rd
    monkeypatch.setattr(rd.RegimeDetector, "detect", lambda self: {"regime": "BULL_TRENDING"})

    result = DriftDetector()._check_regime_drift()
    assert result["detected"] is True
    assert result["trained_on"] == "VOLATILE"
    assert result["current"] == "BULL_TRENDING"


def test_regime_drift_excludes_legacy_records_without_market_regime(tmp_path, monkeypatch):
    """Records predating the `market_regime` field used "regime" to mean the
    stock-level trend (UPTREND/DOWNTREND) — a disjoint vocabulary from market
    regime categories (BULL_TRENDING/VOLATILE/...). They must be excluded from
    the regime-drift population entirely, not inferred/mapped."""
    monkeypatch.chdir(tmp_path)
    # 30 legacy records (no market_regime field, "regime" holds stock trend).
    _write_sims(n_win_first_half=15, n_win_second_half=15,
                regime="UPTREND", tag_market_regime=False)

    import src.data.regime_detector as rd
    monkeypatch.setattr(rd.RegimeDetector, "detect", lambda self: {"regime": "BULL_TRENDING"})

    result = DriftDetector()._check_regime_drift()
    assert result["detected"] is False
    assert "reason" in result


def test_regime_drift_populates_only_market_regime_tagged_records(tmp_path, monkeypatch):
    """A mix of legacy (no market_regime) and new (market_regime present) sim
    records — only the tagged ones should populate the drift check's
    regime_counts / dominant-regime computation."""
    monkeypatch.chdir(tmp_path)
    legacy = [{"outcome": "WIN", "regime": "UPTREND", "rsi": 52,
               "simulated_at": f"2026-01-{i + 1:02d}T00:00:00"} for i in range(10)]
    tagged = [{"outcome": "WIN", "regime": "VOLATILE", "market_regime": "VOLATILE",
               "rsi": 52, "simulated_at": f"2026-02-{i + 1:02d}T00:00:00"}
              for i in range(25)]
    p = Path("data/cache/forced_trades_history.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(legacy + tagged))

    import src.data.regime_detector as rd
    monkeypatch.setattr(rd.RegimeDetector, "detect", lambda self: {"regime": "BULL_TRENDING"})

    result = DriftDetector()._check_regime_drift()
    assert result["trained_on"] == "VOLATILE"  # dominant only among tagged records
    assert result["detected"] is True


def test_confidence_decay_applied(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Force wr_drift by seeding a sharp win-rate collapse.
    _write_sims(n_win_first_half=20, n_win_second_half=5)

    import src.data.regime_detector as rd
    monkeypatch.setattr(rd.RegimeDetector, "detect", lambda self: {"regime": "VOLATILE"})

    detector = DriftDetector()
    detector.journal.log_knowledge_entry(KnowledgeEntry(
        pattern_id="test-pattern", pattern_description="test", confidence=0.80,
        observed_count=15,
    ))

    # WR-drift now requires 2 consecutive detections before it's "confirmed"
    # (a single 30-trade rolling-window dip is noise at this trade volume —
    # see test_drift_decay_cooldown.py) — the first call only builds the streak.
    first = detector.check_and_respond()
    assert first["any_drift"] is True
    assert first["wr_drift"]["confirmed"] is False

    results = detector.check_and_respond()
    assert results["any_drift"] is True
    assert results["wr_drift"]["confirmed"] is True

    kb = detector.journal.get_active_knowledge(min_confidence=0.0)
    entry = next(e for e in kb if e.pattern_id == "test-pattern")
    assert entry.confidence < 0.80
    assert abs(entry.confidence - 0.80 * 0.80) < 1e-6
