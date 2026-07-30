"""Auto-rules Telegram notification (runner.daily_learning_job) must escape
every dynamic string — stock symbols, veto/boost reasons, pattern
descriptions — so a reason containing Markdown special characters
(underscores, parens) never breaks Telegram's legacy-Markdown parser."""
from __future__ import annotations

from unittest.mock import MagicMock

import runner


def _stub(monkeypatch, rules):
    monkeypatch.setattr(
        "src.memory.auto_rules.AutoRuleExtractor",
        lambda: type("E", (), {"extract_and_save": lambda self: rules})())
    monkeypatch.setattr(runner, "DailySynthesizer", lambda: MagicMock(
        synthesize=lambda: MagicMock(patterns_observed="[]", lessons_extracted="[]",
                                     synthesis="")))
    monkeypatch.setattr("src.memory.adaptive_thresholds.AdaptiveThresholds",
                        lambda: MagicMock(update_from_all_trades=lambda: {}))
    monkeypatch.setattr("src.analytics.performance.PerformanceAnalyzer",
                        lambda: MagicMock(get_professional_metrics=lambda: {}))
    monkeypatch.setattr("src.ml.stock_priors.StockPriors",
                        lambda: MagicMock(compute_and_save=lambda: None))
    monkeypatch.setattr(runner, "_run_engine_scorecard", lambda: None)
    monkeypatch.setattr(runner, "_kill_switch", lambda: False)

    sent = []
    monkeypatch.setattr(runner, "TelegramNotifier", lambda: type(
        "N", (), {"send_message": lambda self, text: sent.append(text)})())
    return sent


def test_veto_reason_with_underscores_and_parens_is_escaped(monkeypatch):
    rules = {
        "total_rules": 1,
        "stock_vetoes": {
            "WIPRO": {"reason": "CONT_SIM LOSS: RSI (58) choppy_adx pattern [flag]"},
        },
        "stock_boosts": {},
        "setup_vetoes": [{}],
        "setup_boosts": [],
    }
    sent = _stub(monkeypatch, rules)
    runner.daily_learning_job()

    auto_rules_msgs = [m for m in sent if "Auto-rules updated" in m]
    assert len(auto_rules_msgs) == 1
    msg = auto_rules_msgs[0]
    # The raw unescaped reason string must not appear verbatim — every
    # Markdown-special char (_ * ` [) is backslash-escaped.
    assert "CONT_SIM LOSS" not in msg
    assert "choppy_adx" not in msg
    assert r"CONT\_SIM LOSS" in msg
    assert r"choppy\_adx" in msg
    assert r"\[flag]" in msg  # only "[" needs escaping in Telegram legacy Markdown


def test_symbol_and_reason_escaping_applied_to_boosts_too(monkeypatch):
    rules = {
        "total_rules": 1,
        "stock_vetoes": {},
        "stock_boosts": {
            "AJANTPHARM": {"reason": "Dossier: 7W/8t (88% WR) strong_pattern"},
        },
        "setup_vetoes": [],
        "setup_boosts": [{}],
    }
    sent = _stub(monkeypatch, rules)
    runner.daily_learning_job()

    msg = next(m for m in sent if "Auto-rules updated" in m)
    assert "strong_pattern" not in msg
    assert r"strong\_pattern" in msg
