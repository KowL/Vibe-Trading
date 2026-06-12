"""A-share strategy framework.

Modules:
- multi_factor: Multi-factor stock selection engine
- trend_timing: Trend-following signal generator with adaptive risk
- backtest: Event-driven backtest engine

Usage:
    from src.ashare.strategies import MultiFactorSelector, TrendTiming, MultiFactorBacktest
    selector = MultiFactorSelector()
    pool = selector.select(trade_date=date(2025, 6, 10), top_n=50)

    timing = TrendTiming()
    signals = timing.generate_signals(trade_date=date(2025, 6, 10), stock_pool=pool, ...)

    bt = MultiFactorBacktest()
    result = bt.run(start_date=date(2022, 1, 1), end_date=date(2024, 12, 31))
    print(result.summary())
"""

from src.ashare.strategies.adaptive_risk import BandParams
from src.ashare.strategies.backtest import MultiFactorBacktest
from src.ashare.strategies.fast_backtest import FastMultiFactorBacktest
from src.ashare.strategies.local_loader import LocalKlineLoader
from src.ashare.strategies.multi_factor import MultiFactorSelector, StockScore
from src.ashare.strategies.stock_profile import StockProfile
from src.ashare.strategies.trend_timing import TrendTiming, TradeSignal, Signal, Position

__all__ = [
    "MultiFactorSelector",
    "StockScore",
    "TrendTiming",
    "TradeSignal",
    "Signal",
    "Position",
    "MultiFactorBacktest",
    "FastMultiFactorBacktest",
    "LocalKlineLoader",
    "StockProfile",
    "BandParams",
]
