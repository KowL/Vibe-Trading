"""Multi-factor stock selection engine for A-share market.

Uses Alpha Zoo factors (GTJA191) + trend/momentum/volume filters
to generate a ranked stock pool for trend-following strategy.

Data source: adshare (localhost:8000) with 5-year K-line history.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from src.ashare.adshare_client import AdshareClient
from src.ashare.strategies.stock_names import get_stock_name

logger = logging.getLogger(__name__)


@dataclass
class FactorScore:
    """Score for a single stock on a single factor."""

    symbol: str
    factor_id: str
    value: float
    rank: int  # 1 = best
    percentile: float  # 0-1, 1.0 = best


@dataclass
class StockScore:
    """Composite score for a stock across all factors."""

    symbol: str
    name: str = ""
    factor_scores: dict[str, FactorScore] | None = None
    composite_score: float = 0.0
    composite_rank: int = 0

    # Trend metrics (computed from K-line)
    ma5: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    momentum_20d: float = 0.0  # 20-day return
    volume_ratio: float = 0.0  # today / 20-day avg volume
    atr_14: float = 0.0  # Average True Range

    # Pass/fail flags
    passes_trend: bool = False
    passes_momentum: bool = False
    passes_volume: bool = False
    passes_all: bool = False


class MultiFactorSelector:
    """Select stocks using multi-factor model + trend filters.

    Usage:
        selector = MultiFactorSelector()
        pool = selector.select(
            trade_date=date(2025, 6, 10),
            universe=None,  # all stocks with data
            top_n=50,
            min_composite_score=0.6,
        )
    """

    # Alive factors from Alpha Zoo workflow 1 (2026-06-12)
    # IR > 0.14, confirmed alive on CSI300
    ALIVE_FACTORS: list[dict[str, Any]] = [
        {"id": "gtja191_120", "name": "reversal_vwap_close", "weight": 0.30},
        {"id": "gtja191_114", "name": "volume_volatility", "weight": 0.25},
        {"id": "gtja191_171", "name": "microstructure", "weight": 0.25},
        {"id": "gtja191_111", "name": "volume_micro", "weight": 0.20},
    ]

    # Trend filter thresholds
    TREND_MA_BULLISH = True  # require MA5 > MA20 > MA60
    MOMENTUM_MIN = 0.0  # 20-day return > 0
    VOLUME_RATIO_MIN = 1.0  # volume > 20-day average

    def __init__(self, client: AdshareClient | None = None) -> None:
        self.client = client or AdshareClient()

    def select(
        self,
        trade_date: date,
        universe: list[str] | None = None,
        top_n: int = 50,
        min_composite_score: float = 0.5,
        history_days: int = 120,
    ) -> list[StockScore]:
        """Run full selection pipeline.

        Args:
            trade_date: selection date
            universe: stock codes to consider (None = all available)
            top_n: return top N stocks
            min_composite_score: minimum composite score to pass
            history_days: K-line history needed for factor calc

        Returns:
            List of StockScore, sorted by composite_rank ascending
        """
        # 1. Get universe
        if universe is None:
            universe = self._get_universe()
        logger.info("select: universe=%d stocks", len(universe))

        # 2. Fetch K-line for each stock (parallel)
        begin = (trade_date - timedelta(days=history_days + 30)).strftime("%Y%m%d")
        end = trade_date.strftime("%Y%m%d")

        stock_data: dict[str, pd.DataFrame] = {}
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(self._fetch_kline, symbol, begin, end): symbol
                for symbol in universe
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    df = future.result()
                    if df is not None and len(df) >= 60:
                        stock_data[symbol] = df
                except Exception as exc:
                    logger.debug("skip %s: %s", symbol, exc)

        logger.info("select: loaded K-line for %d stocks", len(stock_data))
        if len(stock_data) < 10:
            logger.warning("Too few stocks with data (%d), aborting", len(stock_data))
            return []

        # 3. Compute factors for each stock
        factor_values: dict[str, dict[str, float]] = {}  # factor_id -> {symbol: value}
        for factor in self.ALIVE_FACTORS:
            fid = factor["id"]
            values: dict[str, float] = {}
            for symbol, df in stock_data.items():
                try:
                    val = self._compute_factor(fid, df)
                    if not np.isnan(val) and not np.isinf(val):
                        values[symbol] = val
                except Exception as exc:
                    logger.debug("factor %s failed for %s: %s", fid, symbol, exc)
            factor_values[fid] = values

        # 4. Rank each factor (cross-sectional)
        all_scores: dict[str, StockScore] = {}
        for symbol in stock_data:
            all_scores[symbol] = StockScore(symbol=symbol, name=get_stock_name(symbol))

        for factor in self.ALIVE_FACTORS:
            fid = factor["id"]
            values = factor_values.get(fid, {})
            if not values:
                continue
            # Rank: higher value = better rank = 1
            sorted_items = sorted(values.items(), key=lambda x: x[1], reverse=True)
            total = len(sorted_items)
            for rank, (symbol, val) in enumerate(sorted_items, 1):
                if symbol not in all_scores:
                    continue
                percentile = (total - rank + 1) / total
                all_scores[symbol].factor_scores = all_scores[symbol].factor_scores or {}
                all_scores[symbol].factor_scores[fid] = FactorScore(
                    symbol=symbol,
                    factor_id=fid,
                    value=val,
                    rank=rank,
                    percentile=percentile,
                )

        # 5. Compute composite score (weighted average of percentiles)
        for symbol, score in all_scores.items():
            if not score.factor_scores:
                continue
            weighted_sum = 0.0
            weight_sum = 0.0
            for factor in self.ALIVE_FACTORS:
                fid = factor["id"]
                weight = factor["weight"]
                fs = score.factor_scores.get(fid) if score.factor_scores else None
                if fs:
                    weighted_sum += fs.percentile * weight
                    weight_sum += weight
            if weight_sum > 0:
                score.composite_score = weighted_sum / weight_sum

        # 6. Compute trend metrics and apply filters
        filtered: list[StockScore] = []
        for symbol, score in all_scores.items():
            df = stock_data.get(symbol)
            if df is None or len(df) < 60:
                continue

            # Compute trend metrics
            score.ma5 = df["close"].iloc[-5:].mean()
            score.ma20 = df["close"].iloc[-20:].mean()
            score.ma60 = df["close"].iloc[-60:].mean()
            score.momentum_20d = (df["close"].iloc[-1] / df["close"].iloc[-20] - 1) * 100
            score.volume_ratio = df["volume"].iloc[-1] / df["volume"].iloc[-20:].mean()

            # ATR
            high_low = df["high"] - df["low"]
            high_close = np.abs(df["high"] - df["close"].shift())
            low_close = np.abs(df["low"] - df["close"].shift())
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            score.atr_14 = tr.iloc[-14:].mean()

            # Apply filters
            score.passes_trend = (
                score.ma5 > score.ma20 > score.ma60
                if self.TREND_MA_BULLISH
                else score.ma5 > score.ma20
            )
            score.passes_momentum = score.momentum_20d > self.MOMENTUM_MIN
            score.passes_volume = score.volume_ratio >= self.VOLUME_RATIO_MIN
            score.passes_all = (
                score.passes_trend
                and score.passes_momentum
                and score.passes_volume
                and score.composite_score >= min_composite_score
            )

            if score.passes_all:
                filtered.append(score)

        # 7. Sort by composite score descending, return top_n
        filtered.sort(key=lambda x: x.composite_score, reverse=True)
        for i, score in enumerate(filtered, 1):
            score.composite_rank = i

        result = filtered[:top_n]
        logger.info(
            "select: %d passed all filters, returning top %d",
            len(filtered),
            len(result),
        )
        return result

    def _get_universe(self) -> list[str]:
        """Get full stock list from adshare."""
        try:
            resp = self.client.get_stock_basic()
            if resp and "data" in resp:
                return [item.get("code", "") for item in resp["data"] if item.get("code")]
        except Exception as exc:
            logger.warning("get_stock_basic failed: %s", exc)
        # Fallback: use a known list of liquid A-share stocks
        return _DEFAULT_UNIVERSE

    def _fetch_kline(self, symbol: str, begin: str, end: str) -> pd.DataFrame | None:
        """Fetch K-line and return as DataFrame."""
        resp = self.client.get_kline(symbol, period="daily", begin_date=begin, end_date=end)
        if not resp or "data" not in resp or not resp["data"]:
            return None
        df = pd.DataFrame(resp["data"])
        df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
        df = df.set_index("date").sort_index()
        return df

    def _compute_factor(self, factor_id: str, df: pd.DataFrame) -> float:
        """Compute a single GTJA191 factor value.

        For now, implements the 4 alive factors.
        Full 452-factor library can be added later.
        """
        close = df["close"]
        volume = df["volume"]
        amount = df["amount"]
        vwap = amount / (volume + 1e-9)  # approximate VWAP

        if factor_id == "gtja191_120":
            # (vwap - close) / (vwap + close) 的 rank
            # 反转因子：价格低于 VWAP 时买入
            val = (vwap.iloc[-1] - close.iloc[-1]) / (vwap.iloc[-1] + close.iloc[-1])
            return val

        elif factor_id == "gtja191_114":
            # volume * volatility correlation
            # 成交量与波动率正相关时信号强
            ret = close.pct_change().iloc[-20:]
            vol = volume.iloc[-20:]
            if len(ret) < 10:
                return 0.0
            return np.corrcoef(ret.fillna(0), vol.fillna(0))[0, 1]

        elif factor_id == "gtja191_171":
            # microstructure: price acceleration
            # 价格加速度 = 二阶导数
            ret = close.pct_change().iloc[-10:]
            if len(ret) < 3:
                return 0.0
            return ret.diff().iloc[-1] * 100  # scale up

        elif factor_id == "gtja191_111":
            # volume + microstructure composite
            # 成交量放大 + 微观结构改善
            vol_ratio = volume.iloc[-1] / volume.iloc[-20:].mean()
            ret = close.pct_change().iloc[-5:]
            price_accel = ret.diff().iloc[-1] if len(ret) >= 2 else 0
            return vol_ratio * price_accel * 10

        else:
            return 0.0


def _compute_factor_value(factor_id: str, df: pd.DataFrame) -> float:
    """Module-level factor computation reused by panel selection."""
    close = df["close"]
    volume = df["volume"]
    amount = df["amount"]
    vwap = amount / (volume + 1e-9)

    if factor_id == "gtja191_120":
        return (vwap.iloc[-1] - close.iloc[-1]) / (vwap.iloc[-1] + close.iloc[-1])
    elif factor_id == "gtja191_114":
        ret = close.pct_change().iloc[-20:]
        vol = volume.iloc[-20:]
        if len(ret) < 10:
            return 0.0
        return np.corrcoef(ret.fillna(0), vol.fillna(0))[0, 1]
    elif factor_id == "gtja191_171":
        ret = close.pct_change().iloc[-10:]
        if len(ret) < 3:
            return 0.0
        return ret.diff().iloc[-1] * 100
    elif factor_id == "gtja191_111":
        vol_ratio = volume.iloc[-1] / volume.iloc[-20:].mean()
        ret = close.pct_change().iloc[-5:]
        price_accel = ret.diff().iloc[-1] if len(ret) >= 2 else 0
        return vol_ratio * price_accel * 10
    return 0.0


def _multi_factor_from_panel(
    panel: dict[str, pd.DataFrame],
    trade_date: date,
    top_n: int,
    factor_weights: dict[str, float] | None = None,
) -> list[StockScore]:
    """Panel-based version of ``MultiFactorSelector.select`` for compare."""
    alive = MultiFactorSelector.ALIVE_FACTORS
    if factor_weights:
        alive = [
            {**f, "weight": factor_weights.get(f["id"], f["weight"])}
            for f in alive
        ]

    td_str = trade_date.strftime("%Y-%m-%d")
    scores: dict[str, StockScore] = {}
    factor_values: dict[str, dict[str, float]] = {f["id"]: {} for f in alive}

    for symbol, df in panel.items():
        if df is None or df.empty:
            continue
        mask = df.index <= td_str
        hist = df[mask]
        if len(hist) < 60:
            continue

        scores[symbol] = StockScore(symbol=symbol, name=get_stock_name(symbol))
        for factor in alive:
            fid = factor["id"]
            try:
                val = _compute_factor_value(fid, hist)
                if not np.isnan(val) and not np.isinf(val):
                    factor_values[fid][symbol] = val
            except Exception:
                continue

    for factor in alive:
        fid = factor["id"]
        values = factor_values.get(fid, {})
        if not values:
            continue
        sorted_items = sorted(values.items(), key=lambda x: x[1], reverse=True)
        total = len(sorted_items)
        for rank, (symbol, val) in enumerate(sorted_items, 1):
            if symbol not in scores:
                continue
            percentile = (total - rank + 1) / total
            scores[symbol].factor_scores = scores[symbol].factor_scores or {}
            scores[symbol].factor_scores[fid] = FactorScore(
                symbol=symbol,
                factor_id=fid,
                value=val,
                rank=rank,
                percentile=percentile,
            )

    for symbol, score in scores.items():
        if not score.factor_scores:
            continue
        weighted_sum = 0.0
        weight_sum = 0.0
        for factor in alive:
            fid = factor["id"]
            weight = factor["weight"]
            fs = score.factor_scores.get(fid)
            if fs:
                weighted_sum += fs.percentile * weight
                weight_sum += weight
        if weight_sum > 0:
            score.composite_score = weighted_sum / weight_sum

    filtered: list[StockScore] = []
    for symbol, score in scores.items():
        df = panel.get(symbol)
        if df is None or df.empty:
            continue
        mask = df.index <= td_str
        hist = df[mask]
        if len(hist) < 60:
            continue

        score.ma5 = hist["close"].iloc[-5:].mean()
        score.ma20 = hist["close"].iloc[-20:].mean()
        score.ma60 = hist["close"].iloc[-60:].mean()
        score.momentum_20d = (hist["close"].iloc[-1] / hist["close"].iloc[-20] - 1) * 100
        score.volume_ratio = hist["volume"].iloc[-1] / hist["volume"].iloc[-20:].mean()

        high_low = hist["high"] - hist["low"]
        high_close = abs(hist["high"] - hist["close"].shift())
        low_close = abs(hist["low"] - hist["close"].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        score.atr_14 = tr.iloc[-14:].mean()

        score.passes_trend = score.ma5 > score.ma20 > score.ma60
        score.passes_momentum = score.momentum_20d > 0
        score.passes_volume = score.volume_ratio >= 1.0
        score.passes_all = (
            score.passes_trend
            and score.passes_momentum
            and score.passes_volume
            and score.composite_score >= 0.5
        )

        if score.passes_all:
            filtered.append(score)

    filtered.sort(key=lambda x: x.composite_score, reverse=True)
    for i, score in enumerate(filtered, 1):
        score.composite_rank = i

    return filtered[:top_n]


def _multi_factor_selector(
    *, trade_date: date, top_n: int, params: dict[str, Any]
) -> list[StockScore]:
    """Registry-compatible wrapper for ``MultiFactorSelector``."""
    panel = params.get("_panel")
    factor_weights = params.get("factor_weights") if isinstance(params.get("factor_weights"), dict) else None
    if panel is not None:
        return _multi_factor_from_panel(panel, trade_date, top_n, factor_weights)
    selector = MultiFactorSelector()
    return selector.select(trade_date=trade_date, top_n=top_n)


def _register_multi_factor() -> None:
    from src.ashare.strategies.selector_registry import register_selector
    register_selector("multi_factor")(_multi_factor_selector)


_register_multi_factor()
del _register_multi_factor


# Fallback universe: 50 most liquid A-share stocks
_DEFAULT_UNIVERSE: list[str] = [
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
