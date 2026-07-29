"""market_open_scan_job/pairs_job must report the actual auto-veto flag name
when that's the rejection cause — not a fabricated numeric score comparison
(previously always printed 'judge X<threshold' even when the real cause was
an auto-veto flag with a high, unrelated overall_score)."""
from __future__ import annotations

import runner


def test_auto_veto_flag_named_not_fabricated_numeric_comparison():
    # A judge verdict rejected purely by an auto-veto flag can carry a high
    # overall_score if it were ever computed after the veto (it isn't in
    # practice — veto forces 0.0 — but the log must not assume otherwise).
    judge = {"approved": False, "overall_score": 8.0, "flags": ["FIGHTING_NIFTY"]}
    detail = runner._judge_rejection_detail(judge)
    assert detail == "FIGHTING_NIFTY"
    assert "<" not in detail  # no fabricated numeric comparison


def test_falls_back_to_numeric_comparison_when_no_flags():
    judge = {"approved": False, "overall_score": 4.2, "flags": []}
    detail = runner._judge_rejection_detail(judge)
    assert "4.2" in detail
    assert "<" in detail


def test_market_open_scan_job_rejection_reads_live_judge_verdict(monkeypatch):
    """The rejection log for the JUDGE gate must come from the SAME judge
    verdict just computed for that candidate this run."""
    calls = []

    class _FakeScout:
        def scan(self):
            return [{"symbol": "GLAND", "sector": "Pharma"}]

    class _FakeFundamental:
        def analyze(self, state):
            return {"hard_rejected": False, "data": {}}

    class _FakeTechnical:
        def analyze(self, state):
            return {"proceed": True, "score": 0.9, "entry_price": 100,
                    "stop_price": 95, "target_price": 110}

    class _FakeJudge:
        def evaluate(self, state):
            verdict = {"approved": False, "overall_score": 9.0, "flags": ["SUPERTREND_BEARISH"]}
            calls.append(verdict)
            return verdict

    monkeypatch.setattr(runner, "ScoutAgent", _FakeScout)
    monkeypatch.setattr(runner, "FundamentalAgent", _FakeFundamental)
    monkeypatch.setattr(runner, "TechnicalAgent", _FakeTechnical)
    monkeypatch.setattr(runner, "LLMJudge", _FakeJudge)
    monkeypatch.setattr(runner, "_kill_switch", lambda: False)
    monkeypatch.setattr(runner, "_is_paused", lambda: False)
    monkeypatch.setattr(runner, "_ScanLock", lambda: __import__("contextlib").nullcontext())

    class _FakeCalendar:
        def is_market_open(self):
            return True

    monkeypatch.setattr("src.data.market_calendar.NSECalendar", _FakeCalendar)

    logged = []
    monkeypatch.setattr(runner.console, "print", lambda msg: logged.append(msg))
    monkeypatch.setattr(runner, "TelegramNotifier", lambda: type(
        "N", (), {"send_trade_card": lambda self, s: None})())
    monkeypatch.setattr(runner, "MarketContext", lambda: type(
        "M", (), {"get_nifty_context": lambda self: {}})())

    runner.market_open_scan_job()

    assert len(calls) == 1
    joined = "\n".join(logged)
    assert "SUPERTREND_BEARISH" in joined
    assert "9.0<" not in joined
