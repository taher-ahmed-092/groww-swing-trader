"""Strategy library + regime-based registry selection."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.memory.journal import TradingJournal
from src.strategies.breakout import BreakoutStrategy
from src.strategies.mean_reversion import MeanReversionStrategy
from src.strategies.momentum import MomentumStrategy
from src.strategies.registry import StrategyRegistry


def _df(last_close=110.0, n=30, highs=None):
    rng = np.random.RandomState(1)
    close = pd.Series(np.linspace(last_close * 0.9, last_close, n))
    high = close + 2 if highs is None else pd.Series(highs)
    return pd.DataFrame({"Open": close, "High": high, "Low": close - 2,
                         "Close": close, "Volume": rng.randint(1e5, 2e5, n)})


def test_momentum_fires_in_uptrend():
    ind = {"trend": "UPTREND", "rsi_14": 58, "adx_14": 30, "ma_50": 105,
           "ma_200": 100, "atr_14": 2.0, "volume_ratio": 1.5, "obv_trend": "RISING"}
    out = MomentumStrategy().generate_signal(_df(110), ind, {})
    assert out["signal"] == "BUY"
    assert out["stop_price"] < out["entry_price"] < out["target_price"]


def test_momentum_skips_downtrend():
    ind = {"trend": "DOWNTREND", "rsi_14": 45, "adx_14": 30, "ma_50": 105, "ma_200": 110}
    assert MomentumStrategy().generate_signal(_df(100), ind, {})["signal"] == "SKIP"


def test_mean_reversion_fires_oversold():
    ind = {"rsi_14": 35, "ma_200": 95, "ma_50": 104, "atr_14": 2.0,
           "support": 99, "bb_lower": 99, "bb_mid": 104, "s1": 99}
    out = MeanReversionStrategy().generate_signal(_df(100), ind, {})
    assert out["signal"] == "BUY"


def test_mean_reversion_skips_downtrend():
    # Below MA200 = falling knife — must not buy.
    ind = {"rsi_14": 30, "ma_200": 110, "atr_14": 2.0, "support": 89, "bb_mid": 95}
    assert MeanReversionStrategy().generate_signal(_df(90), ind, {})["signal"] == "SKIP"


def test_breakout_requires_volume():
    highs = list(np.linspace(108, 112, 29)) + [125]  # last bar breaks out
    ind = {"volume_ratio": 1.0, "atr_14": 2.0}  # weak volume → false-breakout risk
    out = BreakoutStrategy().generate_signal(_df(125, highs=highs), ind, {})
    assert out["signal"] == "SKIP"


def test_breakout_fires_with_volume():
    highs = list(np.linspace(108, 112, 29)) + [125]
    ind = {"volume_ratio": 2.0, "atr_14": 2.0}
    out = BreakoutStrategy().generate_signal(_df(125, highs=highs), ind, {})
    assert out["signal"] == "BUY"


def test_strategy_registry_picks_by_regime(tmp_path):
    reg = StrategyRegistry(journal=TradingJournal(db_path=str(tmp_path / "r.db")))
    bull = [s.name for s in reg.get_strategies_for_regime("BULL_TRENDING")]
    rng = [s.name for s in reg.get_strategies_for_regime("RANGE_BOUND")]
    assert bull[0] in ("momentum", "breakout")
    assert "mean_reversion" not in bull
    assert rng[0] == "mean_reversion"
