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
from src.ashare.strategies.stock_names import get_stock_name

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


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


def _resolve_universe(loader: LocalKlineLoader, universe: list[str] | None) -> list[str]:
    """Resolve universe argument to a concrete list of symbols.

    ``None`` falls back to the default liquid universe.
    ``["all_a"]`` expands to every symbol that has local parquet data.
    """
    if universe is None:
        return _default_universe()
    if universe == ["all_a"]:
        return loader.list_all_symbols()
    return universe


def _load_stock_data(
    trade_date: date,
    data_root: str | None,
    universe: list[str] | None,
    history_days: int,
) -> dict[str, pd.DataFrame]:
    """Load historical data for the requested universe."""
    loader = LocalKlineLoader(data_root)
    resolved = _resolve_universe(loader, universe)
    begin = (trade_date - timedelta(days=history_days + 30)).strftime("%Y%m%d")
    end = trade_date.strftime("%Y%m%d")

    logger.info("select: %d stocks, %s ~ %s", len(resolved), begin, end)

    stock_data = loader.load_batch(resolved, begin, end)
    stock_data = {sym: df for sym, df in stock_data.items() if len(df) >= 60}

    logger.info("select: loaded %d stocks", len(stock_data))
    return stock_data


def _base_metrics(hist: pd.DataFrame) -> dict[str, float]:
    """Compute common trend/volume metrics used by all selectors."""
    return {
        "ma5": float(hist["close"].iloc[-5:].mean()),
        "ma20": float(hist["close"].iloc[-20:].mean()),
        "ma60": float(hist["close"].iloc[-60:].mean()),
        "momentum_20d": float((hist["close"].iloc[-1] / hist["close"].iloc[-20] - 1) * 100),
        "volume_ratio": float(hist["volume"].iloc[-1] / hist["volume"].iloc[-20:].mean()),
    }


def _atr_14(hist: pd.DataFrame) -> float:
    """Compute 14-day Average True Range."""
    high_low = hist["high"] - hist["low"]
    high_close = abs(hist["high"] - hist["close"].shift())
    low_close = abs(hist["low"] - hist["close"].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return float(tr.iloc[-14:].mean())


def _volatility(hist: pd.DataFrame) -> float:
    """Annualized volatility (%)."""
    returns = hist["close"].pct_change().dropna()
    return float(returns.std() * np.sqrt(252) * 100)


def _rsi(close: pd.Series, window: int = 14) -> float:
    """Compute RSI for the close price series."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / (loss + 1e-12)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0


def _build_stock_score(symbol: str, hist: pd.DataFrame, composite: float) -> StockScore:
    """Build a StockScore from a price history and composite score."""
    metrics = _base_metrics(hist)
    return StockScore(
        symbol=symbol,
        name=get_stock_name(symbol),
        composite_score=composite,
        ma5=metrics["ma5"],
        ma20=metrics["ma20"],
        ma60=metrics["ma60"],
        momentum_20d=metrics["momentum_20d"],
        volume_ratio=metrics["volume_ratio"],
        atr_14=_atr_14(hist),
    )


# --------------------------------------------------------------------------- #
# Selectors
# --------------------------------------------------------------------------- #


def local_select(
    trade_date: date,
    data_root: str | None = None,
    universe: list[str] | None = None,
    top_n: int = 20,
    history_days: int = 120,
) -> list[StockScore]:
    """Multi-factor selection: momentum + volume + trend + volatility.

    Args:
        trade_date: selection date
        data_root: path to local A-share data root (default: /Volumes/mm/project/adshare/data)
        universe: stock codes to consider (None = default universe, ["all_a"] = all local data)
        top_n: return top N stocks
        history_days: K-line history needed

    Returns:
        List of StockScore, sorted by composite_score descending
    """
    stock_data = _load_stock_data(trade_date, data_root, universe, history_days)
    if len(stock_data) < 10:
        return []

    td_str = trade_date.strftime("%Y-%m-%d")
    scores: list[StockScore] = []

    for symbol, df in stock_data.items():
        hist = df[df.index <= td_str]
        if len(hist) < 60:
            continue

        m = _base_metrics(hist)
        ma5, ma20, ma60 = m["ma5"], m["ma20"], m["ma60"]
        momentum_20d = m["momentum_20d"]
        volume_ratio = m["volume_ratio"]

        # Filters
        if ma5 <= ma20 or momentum_20d <= 0 or volume_ratio < 1.0:
            continue

        # Factor scoring
        momentum_score = min(momentum_20d / 20.0, 1.0)
        volume_score = max(0.0, min(1.0, 1.0 - abs(volume_ratio - 2.0) / 3.0))
        trend_score = 1.0 if ma5 > ma20 > ma60 else 0.7 if ma5 > ma20 else 0.5
        vol_score = max(0.0, 1.0 - _volatility(hist) / 50.0)

        composite = (
            momentum_score * 0.35 +
            volume_score * 0.25 +
            trend_score * 0.25 +
            vol_score * 0.15
        )

        scores.append(_build_stock_score(symbol, hist, composite))

    scores.sort(key=lambda x: x.composite_score, reverse=True)
    return scores[:top_n]


def trend_select(
    trade_date: date,
    data_root: str | None = None,
    universe: list[str] | None = None,
    top_n: int = 20,
    history_days: int = 120,
) -> list[StockScore]:
    """Trend-momentum selection: stronger filters for breakout-style stocks.

    Only keeps stocks with clear upward momentum and expanding volume,
    then ranks by raw momentum.
    """
    stock_data = _load_stock_data(trade_date, data_root, universe, history_days)
    if len(stock_data) < 10:
        return []

    td_str = trade_date.strftime("%Y-%m-%d")
    scores: list[StockScore] = []

    for symbol, df in stock_data.items():
        hist = df[df.index <= td_str]
        if len(hist) < 60:
            continue

        m = _base_metrics(hist)
        ma5, ma20, ma60 = m["ma5"], m["ma20"], m["ma60"]
        momentum_20d = m["momentum_20d"]
        volume_ratio = m["volume_ratio"]

        # Stronger trend filters
        if not (ma5 > ma20 > ma60):
            continue
        if momentum_20d < 5.0:
            continue
        if volume_ratio < 1.5:
            continue

        # Score emphasises momentum and volume expansion
        composite = min(momentum_20d / 30.0, 1.0) * 0.5 + min(volume_ratio / 4.0, 1.0) * 0.3 + 0.2
        scores.append(_build_stock_score(symbol, hist, composite))

    scores.sort(key=lambda x: x.composite_score, reverse=True)
    return scores[:top_n]


def mean_reversion_select(
    trade_date: date,
    data_root: str | None = None,
    universe: list[str] | None = None,
    top_n: int = 20,
    history_days: int = 120,
) -> list[StockScore]:
    """Mean-reversion selection: stocks that have pulled back but are bouncing.

    Looks for short-term oversold conditions (price below MA20, RSI < 45)
    while the longer-term trend is still intact (above MA60) and volume is
    starting to pick up.
    """
    stock_data = _load_stock_data(trade_date, data_root, universe, history_days)
    if len(stock_data) < 10:
        return []

    td_str = trade_date.strftime("%Y-%m-%d")
    scores: list[StockScore] = []

    for symbol, df in stock_data.items():
        hist = df[df.index <= td_str]
        if len(hist) < 60:
            continue

        close = hist["close"]
        m = _base_metrics(hist)
        ma20, ma60 = m["ma20"], m["ma60"]
        last = close.iloc[-1]
        rsi = _rsi(close)

        # Pullback but not broken: price below MA20 but above MA60
        if last >= ma20 or last <= ma60:
            continue
        if rsi >= 45:
            continue
        if m["momentum_20d"] <= -15:
            continue  # avoid falling knives
        if m["volume_ratio"] < 1.0:
            continue  # need some volume confirmation

        # Score: deeper pullback + higher volume = stronger mean-reversion candidate
        pullback_pct = (ma20 - last) / ma20 * 100
        composite = pullback_pct * 0.4 + m["volume_ratio"] * 0.3 + (45 - rsi) / 45 * 0.3
        scores.append(_build_stock_score(symbol, hist, composite))

    scores.sort(key=lambda x: x.composite_score, reverse=True)
    return scores[:top_n]


# --------------------------------------------------------------------------- #
# Registry / panel helpers (kept for strategy-compare compatibility)
# --------------------------------------------------------------------------- #


def _local_select_from_panel(
    panel: dict[str, "pd.DataFrame"], trade_date: date, top_n: int
) -> list[StockScore]:
    """Panel-based version of ``local_select`` used by strategy compare."""
    scores: list[StockScore] = []
    td_str = trade_date.strftime("%Y-%m-%d")

    for symbol, df in panel.items():
        if df is None or df.empty:
            continue
        mask = df.index <= td_str
        hist = df[mask]
        if len(hist) < 60:
            continue

        ma5 = hist["close"].iloc[-5:].mean()
        ma20 = hist["close"].iloc[-20:].mean()
        ma60 = hist["close"].iloc[-60:].mean()
        momentum_20d = (hist["close"].iloc[-1] / hist["close"].iloc[-20] - 1) * 100
        volume_ratio = hist["volume"].iloc[-1] / hist["volume"].iloc[-20:].mean()

        if ma5 <= ma20 or momentum_20d <= 0 or volume_ratio < 1.0:
            continue

        momentum_score = min(momentum_20d / 20.0, 1.0)
        volume_score = 1.0 - abs(volume_ratio - 2.0) / 3.0
        volume_score = max(0, min(volume_score, 1.0))
        trend_score = 0.5
        if ma5 > ma20 > ma60:
            trend_score = 1.0
        elif ma5 > ma20:
            trend_score = 0.7

        returns = hist["close"].pct_change().dropna()
        volatility = returns.std() * np.sqrt(252) * 100
        vol_score = max(0, 1.0 - volatility / 50.0)

        composite = (
            momentum_score * 0.35 +
            volume_score * 0.25 +
            trend_score * 0.25 +
            vol_score * 0.15
        )

        high_low = hist["high"] - hist["low"]
        high_close = abs(hist["high"] - hist["close"].shift())
        low_close = abs(hist["low"] - hist["close"].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr_14 = tr.iloc[-14:].mean()

        scores.append(StockScore(
            symbol=symbol,
            name=get_stock_name(symbol),
            composite_score=composite,
            ma5=ma5,
            ma20=ma20,
            ma60=ma60,
            momentum_20d=momentum_20d,
            volume_ratio=volume_ratio,
            atr_14=atr_14,
        ))

    scores.sort(key=lambda x: x.composite_score, reverse=True)
    return scores[:top_n]


def _local_select_selector(
    *, trade_date: date, top_n: int, params: dict[str, Any]
) -> list[StockScore]:
    """Registry-compatible wrapper for ``local_select``."""
    panel = params.get("_panel")
    if panel is not None:
        return _local_select_from_panel(panel, trade_date, top_n)
    universe = params.get("_universe")
    return local_select(trade_date=trade_date, top_n=top_n, universe=universe)


def _register_local_select() -> None:
    from src.ashare.strategies.selector_registry import register_selector
    register_selector("local_select")(_local_select_selector)


_register_local_select()
del _register_local_select
