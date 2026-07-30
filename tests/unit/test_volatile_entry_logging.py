"""Every entry attempt under regime==VOLATILE must log an explicit
'[ENTRY] VOLATILE regime, threshold X, candidate scored Y' line — across
the real judge path, the forced-learning path, and the continuous-sim
path — so the adaptive threshold's actual effect (or lack of it) at entry
is auditable, not just inferred from the aggregate win-rate stat."""
from __future__ import annotations

from unittest.mock import patch

from src.judge.evaluator import LLMJudge


def test_judge_logs_volatile_entry_in_demo_mode(capsys):
    with patch("src.judge.evaluator.settings") as mock_settings, \
         patch("src.data.regime_detector.RegimeDetector") as mock_regime, \
         patch("src.memory.adaptive_thresholds.AdaptiveThresholds") as mock_thresh:
        mock_settings.effective_demo_mode = True
        mock_regime.return_value.detect.return_value = {"regime": "VOLATILE"}
        mock_thresh.return_value.get_judge_threshold.return_value = 7.5

        state = {
            "symbol": "TEST",
            "fundamental_verdict": {"score": 0.7, "hard_rejected": False},
            "technical_verdict": {
                "score": 0.6, "signal": "BUY", "proceed": True,
                "entry_price": 100.0, "stop_price": 95.0, "target_price": 110.0,
                "indicators": {"adx_signal": "TRENDING"},
            },
            "market_context": {"nifty_trend": "UPTREND"},
        }
        LLMJudge().evaluate(state)

    out = capsys.readouterr().out
    assert "[ENTRY] VOLATILE regime" in out
    assert "threshold" in out
    assert "candidate scored" in out


def test_no_volatile_log_for_other_regimes(capsys):
    with patch("src.judge.evaluator.settings") as mock_settings, \
         patch("src.data.regime_detector.RegimeDetector") as mock_regime, \
         patch("src.memory.adaptive_thresholds.AdaptiveThresholds") as mock_thresh:
        mock_settings.effective_demo_mode = True
        mock_regime.return_value.detect.return_value = {"regime": "BULL_TRENDING"}
        mock_thresh.return_value.get_judge_threshold.return_value = 6.5

        state = {
            "symbol": "TEST",
            "fundamental_verdict": {"score": 0.7, "hard_rejected": False},
            "technical_verdict": {
                "score": 0.6, "signal": "BUY", "proceed": True,
                "entry_price": 100.0, "stop_price": 95.0, "target_price": 110.0,
                "indicators": {"adx_signal": "TRENDING"},
            },
            "market_context": {"nifty_trend": "UPTREND"},
        }
        LLMJudge().evaluate(state)

    out = capsys.readouterr().out
    assert "[ENTRY] VOLATILE" not in out


def test_always_on_trader_logs_volatile_entry_not_consulted(caplog):
    from src.trading.always_on_trader import AlwaysOnTrader

    with patch("src.data.fetcher.MarketDataFetcher") as mock_fetcher_cls, \
         patch("src.trading.always_on_trader.compute_indicators") as mock_ind, \
         patch("src.memory.adaptive_thresholds.AdaptiveThresholds") as mock_thresh:
        import pandas as pd

        df = pd.DataFrame({"Close": [100.0] * 35, "Volume": [1000] * 35})
        mock_fetcher_cls.return_value.get_price_history.return_value = df
        mock_ind.return_value = {
            "supertrend_direction": "BULLISH", "obv_trend": "RISING",
            "price_vs_vwap": "ABOVE", "rsi_14": 55,
        }
        mock_thresh.return_value.load.return_value = {"VOLATILE": {"win_rate": 0.5}}
        mock_thresh.return_value.get_judge_threshold.return_value = 7.5

        trader = AlwaysOnTrader()
        import logging

        with caplog.at_level(logging.INFO):
            # Directly exercise via a minimal monkeypatched sample to avoid a
            # full random.sample over the real 200+ stock universe.
            with patch("src.trading.always_on_trader.random.sample", return_value=["RELIANCE"]):
                with patch.object(trader, "_current_regime", return_value="VOLATILE"):
                    trader._score_all_candidates_live()

    assert any("[ENTRY] VOLATILE regime" in r.message for r in caplog.records)
    assert any("not consulted here" in r.message for r in caplog.records)
