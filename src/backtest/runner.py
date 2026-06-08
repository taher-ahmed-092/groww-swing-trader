"""
Backtest harness around the backtesting.py library.

This is a baseline strategy for validating the data pipeline. The real strategy
logic will evolve from the live agent decisions — this just confirms data flows
end to end and gives a sanity benchmark.

Strategy: buy when RSI(14) < 35 and price > MA200; exit when RSI > 65 or the 7%
stop (from config.risk_limits) is hit.
"""
from __future__ import annotations

import pandas as pd
from backtesting import Backtest, Strategy
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator

from config.risk_limits import LIMITS
from src.data.fetcher import MarketDataFetcher


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    return RSIIndicator(pd.Series(close), window=window).rsi()


def _sma(close: pd.Series, window: int) -> pd.Series:
    return SMAIndicator(pd.Series(close), window=window).sma_indicator()


class _RsiMeanReversion(Strategy):
    rsi_buy = 35
    rsi_sell = 65

    def init(self) -> None:
        close = self.data.Close
        self.rsi = self.I(_rsi, close, 14)
        self.ma200 = self.I(_sma, close, 200)

    def next(self) -> None:
        price = self.data.Close[-1]
        ma200 = self.ma200[-1]

        if not self.position:
            if self.rsi[-1] < self.rsi_buy and ma200 == ma200 and price > ma200:
                stop = price * (1 - LIMITS.stop_loss_pct / 100)
                self.buy(sl=stop)
        else:
            if self.rsi[-1] > self.rsi_sell:
                self.position.close()


class BacktestRunner:
    def __init__(self) -> None:
        self.fetcher = MarketDataFetcher()

    def run(self, symbol: str, period: str = "2y") -> dict:
        df = self.fetcher.get_price_history(symbol, period=period)
        if df is None or df.empty or len(df) < 200:
            return {
                "symbol": symbol,
                "total_return_pct": None,
                "win_rate": None,
                "num_trades": 0,
                "sharpe": None,
                "max_drawdown": None,
                "note": "insufficient data (need >= 200 rows)",
            }

        data = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        bt = Backtest(data, _RsiMeanReversion, cash=10_000, commission=0.001)
        stats = bt.run()

        return {
            "symbol": symbol,
            "total_return_pct": round(float(stats.get("Return [%]", 0.0)), 4),
            "win_rate": round(float(stats.get("Win Rate [%]", 0.0)), 4),
            "num_trades": int(stats.get("# Trades", 0)),
            "sharpe": round(float(stats.get("Sharpe Ratio", 0.0)), 4),
            "max_drawdown": round(float(stats.get("Max. Drawdown [%]", 0.0)), 4),
        }
