"""Quick multi-factor selection using local data.

Usage:
    from src.ashare.strategies.local_select import local_select
    pool = local_select(trade_date=date(2025, 6, 12), top_n=20)
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from src.ashare.strategies.local_loader import LocalKlineLoader
from src.ashare.strategies.multi_factor import StockScore

logger = logging.getLogger(__name__)


def local_select(
    trade_date: date,
    data_root: str | None = None,
    universe: list[str] | None = None,
    top_n: int = 20,
    history_days: int = 120,
) -> list[StockScore]:
    """Select stocks using local DuckDB/Parquet data.

    Args:
        trade_date: selection date
        data_root: path to adshare data root (default: /Volumes/mm/project/adshare/data)
        universe: stock codes to consider (None = default universe)
        top_n: return top N stocks
        history_days: K-line history needed

    Returns:
        List of StockScore, sorted by composite_score descending
    """
    loader = LocalKlineLoader(data_root)

    if universe is None:
        universe = _default_universe()

    begin = (trade_date - timedelta(days=history_days + 30)).strftime("%Y%m%d")
    end = trade_date.strftime("%Y%m%d")

    logger.info("local_select: %d stocks, %s ~ %s", len(universe), begin, end)

    # Load all data in a single DuckDB query (faster and thread-safe)
    stock_data = loader.load_batch(universe, begin, end)
    stock_data = {sym: df for sym, df in stock_data.items() if len(df) >= 60}

    logger.info("local_select: loaded %d stocks", len(stock_data))
    if len(stock_data) < 10:
        return []

    # Compute scores
    scores: list[StockScore] = []
    td_str = trade_date.strftime("%Y-%m-%d")

    for symbol, df in stock_data.items():
        mask = df.index <= td_str
        hist = df[mask]
        if len(hist) < 60:
            continue

        # Trend metrics
        ma5 = hist["close"].iloc[-5:].mean()
        ma20 = hist["close"].iloc[-20:].mean()
        ma60 = hist["close"].iloc[-60:].mean()
        momentum_20d = (hist["close"].iloc[-1] / hist["close"].iloc[-20] - 1) * 100
        volume_ratio = hist["volume"].iloc[-1] / hist["volume"].iloc[-20:].mean()

        # Filters
        if ma5 <= ma20 or momentum_20d <= 0 or volume_ratio < 1.0:
            continue

        # Factor-like scoring (simplified)
        # 1. Momentum score (higher is better)
        momentum_score = min(momentum_20d / 20.0, 1.0)  # cap at 20%

        # 2. Volume score (higher is better, but not too high)
        volume_score = 1.0 - abs(volume_ratio - 2.0) / 3.0  # optimal around 2x
        volume_score = max(0, min(volume_score, 1.0))

        # 3. Trend strength (MA alignment)
        trend_score = 0.5
        if ma5 > ma20 > ma60:
            trend_score = 1.0
        elif ma5 > ma20:
            trend_score = 0.7

        # 4. Volatility penalty (lower volatility preferred)
        returns = hist["close"].pct_change().dropna()
        volatility = returns.std() * np.sqrt(252) * 100
        vol_score = max(0, 1.0 - volatility / 50.0)  # penalty above 50% vol

        # Composite score (weighted)
        composite = (
            momentum_score * 0.35 +
            volume_score * 0.25 +
            trend_score * 0.25 +
            vol_score * 0.15
        )

        # ATR for position sizing
        high_low = hist["high"] - hist["low"]
        high_close = abs(hist["high"] - hist["close"].shift())
        low_close = abs(hist["low"] - hist["close"].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr_14 = tr.iloc[-14:].mean()

        scores.append(StockScore(
            symbol=symbol,
            composite_score=composite,
            ma5=ma5,
            ma20=ma20,
            ma60=ma60,
            momentum_20d=momentum_20d,
            volume_ratio=volume_ratio,
            atr_14=atr_14,
        ))

    # Sort by composite score descending
    scores.sort(key=lambda x: x.composite_score, reverse=True)
    return scores[:top_n]


def _default_universe() -> list[str]:
    """Default stock universe (top 80 liquid A-shares)."""
    return [
        "000001.SZ", "000002.SZ", "000063.SZ", "000100.SZ", "000333.SZ",
        "000538.SZ", "000568.SZ", "000651.SZ", "000725.SZ", "000768.SZ",
        "000858.SZ", "000895.SZ", "002001.SZ", "002007.SZ", "002024.SZ",
        "002027.SZ", "002142.SZ", "002230.SZ", "002236.SZ", "002415.SZ",
        "002460.SZ", "002475.SZ", "002594.SZ", "002714.SZ", "300014.SZ",
        "300015.SZ", "300033.SZ", "300059.SZ", "300122.SZ", "300124.SZ",
        "300274.SZ", "300408.SZ", "300433.SZ", "300750.SZ", "600000.SH",
        "600009.SH", "600016.SH", "600028.SH", "600030.SH", "600031.SH",
        "600036.SH", "600048.SH", "600104.SH", "600196.SH", "600276.SH",
        "600309.SH", "600406.SH", "600436.SH", "600519.SH", "600585.SH",
        "600690.SH", "600703.SH", "600745.SH", "600809.SH", "600837.SH",
        "600887.SH", "600900.SH", "601012.SH", "601066.SH", "601088.SH",
        "601166.SH", "601211.SH", "601318.SH", "601336.SH", "601398.SH",
        "601601.SH", "601628.SH", "601668.SH", "601688.SH", "601766.SH",
        "601857.SH", "601888.SH", "601899.SH", "601919.SH", "601995.SH",
        "603259.SH", "603288.SH", "603501.SH", "603986.SH", "605117.SH",
        "688111.SH", "688981.SH",
    ]
