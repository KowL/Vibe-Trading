"""Strategy runners for the A-share strategy market.

Each strategy in the catalogue has a runner function registered with the
`strategy_registry`.  Runners read real local parquet data via `LocalKlineLoader`
and return a `StrategySnapshot` that the market engine caches and publishes.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Literal

import numpy as np
import pandas as pd

from src.ashare.strategies.adaptive_backtest import AdaptiveBacktest
from src.ashare.strategies.fast_backtest import FastMultiFactorBacktest
from src.ashare.strategies.local_loader import LocalKlineLoader
from src.ashare.strategies.local_select import local_select, mean_reversion_select, trend_select
from src.ashare.strategies.market_models import (
    MatchedSymbol,
    StrategyCategory,
    StrategyDefinition,
    StrategyMetrics,
    StrategyParam,
    StrategyRunRequest,
    StrategySnapshot,
)
from src.ashare.strategies.stock_names import get_stock_name
from src.ashare.strategies.stock_profile import StockProfile
from src.ashare.strategies.strategy_registry import register_strategy

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _detect_latest_trade_date(data_root: str | None = None) -> date:
    """Find the latest date with data using a benchmark symbol."""
    loader = LocalKlineLoader(data_root)
    today = date.today()
    begin = (today - timedelta(days=30)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    df = loader.load("000001.SZ", begin, end)
    if df is not None and not df.empty:
        latest = df.index[-1]
        return latest.date() if hasattr(latest, "date") else latest
    return today


def _market_date(request: StrategyRunRequest, data_root: str | None = None) -> date:
    if request.market_date:
        return request.market_date
    return _detect_latest_trade_date(data_root)


def _data_root(request: StrategyRunRequest) -> str | None:
    return request.params.get("data_root")


def _int_param(params: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(params.get(key, default))
    except Exception:
        return default


def _float_param(params: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(params.get(key, default))
    except Exception:
        return default


def _universe_param(params: dict[str, Any]) -> list[str] | None:
    universe = params.get("universe")
    if isinstance(universe, list) and universe:
        return [str(s) for s in universe]
    return None


def _backtest_range(market_date: date) -> tuple[date, date]:
    """Use the trailing 12 months for strategy backtests."""
    start = date(market_date.year - 1, market_date.month, market_date.day)
    # avoid weekends if landed there
    while start.weekday() >= 5:
        start += timedelta(days=1)
    return start, market_date


def _result_to_metrics(result: Any) -> StrategyMetrics:
    """Convert a backtest result dataclass to StrategyMetrics."""
    return StrategyMetrics(
        total_return_pct=getattr(result, "total_return_pct", 0.0),
        annualized_return_pct=getattr(result, "annualized_return_pct", 0.0),
        max_drawdown_pct=getattr(result, "max_drawdown_pct", 0.0),
        sharpe_ratio=getattr(result, "sharpe_ratio", 0.0),
        win_rate=getattr(result, "win_rate", 0.0),
        profit_factor=getattr(result, "profit_factor", 0.0),
        num_trades=getattr(result, "num_trades", 0),
        avg_holding_days=getattr(result, "avg_holding_days", 0.0),
    )


def _empty_snapshot(
    strategy_id: str, market_date: date | None, status: str, error: str | None = None
) -> StrategySnapshot:
    return StrategySnapshot(
        strategy_id=strategy_id,
        run_at=datetime.now(),
        status=status,  # type: ignore[arg-type]
        market_date=market_date,
        matched=[],
        error=error,
    )


def _build_action_suggestion(strategy_id: str, signal: str, metadata: dict[str, Any]) -> str:
    """Generate a dynamic, strategy-aware action suggestion from matched metadata.

    The suggestion combines the strategy logic with per-stock indicators so the
    user gets actionable guidance rather than a static label.
    """
    score = float(metadata.get("score") or 0)
    confidence = float(metadata.get("confidence") or 0)
    momentum_20d = float(metadata.get("momentum_20d") or 0)
    volume_ratio = float(metadata.get("volume_ratio") or 0)

    if strategy_id == "local_selector":
        if signal == "buy":
            if score >= 0.9 and volume_ratio >= 2.0:
                return "评分高且量能充沛，今日可作为买入候选，轻仓试探，跌破MA5止损"
            if momentum_20d >= 20:
                return "评分靠前且动量较强，今日可作为买入候选，回调至MA5附近低吸"
            return "评分达到买入阈值，今日可作为买入候选，注意仓位控制"
        if score >= 0.7:
            return "综合评分良好，纳入观察池，放量突破后再考虑介入"
        return "排名一般，建议观望"

    if strategy_id == "trend_timing":
        if score >= 0.9 and volume_ratio >= 2.0:
            return "强势突破且明显放量，可追涨参与，仓位控制在较轻水平"
        if score >= 0.8 and momentum_20d >= 15:
            return "趋势较强，建议逢低布局，止损设在MA20下方"
        if score >= 0.7:
            return "趋势信号成立，可小仓位试盘，跌破MA20离场"
        return "趋势较弱，建议等待更明确信号"

    if strategy_id == "bollinger_band":
        upper = float(metadata.get("upper") or 0)
        lower = float(metadata.get("lower") or 0)
        close = float(metadata.get("close") or 0)
        if signal == "buy" and upper > lower > 0:
            pct = (close - lower) / (upper - lower)
            if close <= lower * 0.98:
                return "价格显著跌破下轨，超卖明显，关注反弹机会，止损设在下轨下方"
            if pct <= 0.1:
                return "价格触及下轨，可轻仓低吸，目标看向中轨"
            return "价格接近下轨，关注支撑位是否有效"
        if signal == "sell" and upper > lower > 0:
            pct = (close - lower) / (upper - lower)
            if close >= upper * 1.02:
                return "价格显著突破上轨，超买明显，建议减仓止盈"
            if pct >= 0.9:
                return "价格触及上轨，建议考虑止盈或减仓"
            return "价格接近上轨，关注压力位表现"
        return "价格在布林带中轨附近，观望为主"

    if strategy_id == "adaptive_personality":
        if signal == "buy":
            if score and score >= 50 and volume_ratio >= 2.0:
                return "自适应选股评分高且量能充沛，今日可作为买入候选，按股性设置止损"
            if momentum_20d >= 20:
                return "动量较强且通过自适应过滤，今日可作为买入候选，注意仓位控制"
            return "通过自适应选股过滤，今日可作为买入候选，结合大盘环境决策"
        return "建议观望"

    # Fallback generic suggestion based on signal
    if signal == "buy":
        return "触发买入信号，建议结合仓位管理参与"
    if signal == "sell":
        return "触发卖出信号，建议止盈或减仓"
    if signal == "hold":
        return "建议继续持有，关注止损止盈位"
    return "建议纳入观察，等待更明确信号"


# --------------------------------------------------------------------------- #
# Runner: multi-factor selector
# --------------------------------------------------------------------------- #


def _run_selector(request: StrategyRunRequest) -> StrategySnapshot:
    market_date = _market_date(request, _data_root(request))
    top_n = _int_param(request.params, "top_n", 20)
    try:
        pool = local_select(
            trade_date=market_date,
            data_root=_data_root(request),
            universe=_universe_param(request.params) or ["all_a"],
            top_n=top_n,
        )
        matched = []
        for idx, s in enumerate(pool, start=1):
            score = round(s.composite_score, 4) if s.composite_score else None
            confidence = round(min(0.99, s.composite_score or 0.0), 4)
            momentum_20d = round(s.momentum_20d, 2)
            volume_ratio = round(s.volume_ratio, 2)
            # High-score + volume-confirmed candidates are treated as actionable buy signals
            signal: Literal["buy", "watch"] = (
                "buy" if (score or 0) >= 0.8 and volume_ratio >= 1.5 else "watch"
            )
            meta = {
                "score": score,
                "confidence": confidence,
                "momentum_20d": momentum_20d,
                "volume_ratio": volume_ratio,
                "ma5": round(float(s.ma5), 2),
                "ma20": round(float(s.ma20), 2),
                "ma60": round(float(s.ma60), 2),
            }
            meta["action_suggestion"] = _build_action_suggestion(
                "local_selector", signal, meta
            )
            matched.append(
                MatchedSymbol(
                    symbol=s.symbol,
                    name=s.name or get_stock_name(s.symbol),
                    signal=signal,
                    score=score,
                    confidence=confidence,
                    rank=idx,
                    metadata=meta,
                )
            )

        metrics: StrategyMetrics | None = None
        curve: list[dict[str, Any]] | None = None
        if request.run_backtest:
            try:
                start, end = _backtest_range(market_date)
                bt = FastMultiFactorBacktest(data_root=_data_root(request))
                bt.preload_data(start_date=start, end_date=end)
                result = bt.run(start_date=start, end_date=end)
                metrics = _result_to_metrics(result)
                curve = [
                    {
                        "date": e["date"],
                        "value": round(e["total_value"], 2),
                        "drawdown_pct": round(e["drawdown_pct"], 2),
                    }
                    for e in result.equity_curve
                ]
            except Exception as exc:
                logger.warning("selector backtest failed: %s", exc)

        return StrategySnapshot(
            strategy_id=request.strategy_id,
            run_at=datetime.now(),
            status="success",
            market_date=market_date,
            matched=matched,
            metrics=metrics,
            backtest_curve=curve,
        )
    except Exception as exc:
        logger.exception("selector run failed")
        return _empty_snapshot(request.strategy_id, market_date, "error", str(exc))


# --------------------------------------------------------------------------- #
# Runner: trend timing
# --------------------------------------------------------------------------- #


def _run_timing(request: StrategyRunRequest) -> StrategySnapshot:
    market_date = _market_date(request, _data_root(request))
    top_n = _int_param(request.params, "top_n", 20)
    try:
        pool = trend_select(
            trade_date=market_date,
            data_root=_data_root(request),
            universe=_universe_param(request.params) or ["all_a"],
            top_n=top_n,
        )
        matched: list[MatchedSymbol] = []
        for idx, s in enumerate(pool, start=1):
            score = round(s.composite_score, 4)
            confidence = round(min(0.99, s.composite_score), 4)
            meta = {
                "score": score,
                "confidence": confidence,
                "momentum_20d": round(s.momentum_20d, 2),
                "volume_ratio": round(s.volume_ratio, 2),
                "ma5": round(float(s.ma5), 2),
                "ma20": round(float(s.ma20), 2),
                "ma60": round(float(s.ma60), 2),
            }
            meta["action_suggestion"] = _build_action_suggestion(
                "trend_timing", "buy", meta
            )
            matched.append(
                MatchedSymbol(
                    symbol=s.symbol,
                    name=s.name or get_stock_name(s.symbol),
                    signal="buy",
                    score=score,
                    confidence=confidence,
                    rank=idx,
                    metadata=meta,
                )
            )

        metrics: StrategyMetrics | None = None
        curve: list[dict[str, Any]] | None = None
        if request.run_backtest:
            try:
                start, end = _backtest_range(market_date)
                bt = FastMultiFactorBacktest(data_root=_data_root(request))
                bt.preload_data(start_date=start, end_date=end)
                result = bt.run(start_date=start, end_date=end)
                metrics = _result_to_metrics(result)
                curve = [
                    {
                        "date": e["date"],
                        "value": round(e["total_value"], 2),
                        "drawdown_pct": round(e["drawdown_pct"], 2),
                    }
                    for e in result.equity_curve
                ]
            except Exception as exc:
                logger.warning("timing backtest failed: %s", exc)

        return StrategySnapshot(
            strategy_id=request.strategy_id,
            run_at=datetime.now(),
            status="success",
            market_date=market_date,
            matched=matched,
            metrics=metrics,
            backtest_curve=curve,
        )
    except Exception as exc:
        logger.exception("timing run failed")
        return _empty_snapshot(request.strategy_id, market_date, "error", str(exc))


# --------------------------------------------------------------------------- #
# Runner: Bollinger band
# --------------------------------------------------------------------------- #


def _run_band(request: StrategyRunRequest) -> StrategySnapshot:
    market_date = _market_date(request, _data_root(request))
    top_n = _int_param(request.params, "top_n", 20)
    window = _int_param(request.params, "band_window", 20)
    width = _float_param(request.params, "band_width", 2.0)
    try:
        pool = mean_reversion_select(
            trade_date=market_date,
            data_root=_data_root(request),
            universe=_universe_param(request.params) or ["all_a"],
            top_n=top_n * 3,
        )
        loader = LocalKlineLoader(_data_root(request))
        begin = (market_date - timedelta(days=window + 30)).strftime("%Y%m%d")
        end = market_date.strftime("%Y%m%d")

        matched: list[MatchedSymbol] = []
        for idx, s in enumerate(pool, start=1):
            df = loader.load(s.symbol, begin, end)
            if df is None or len(df) < window + 5:
                continue
            td_str = market_date.strftime("%Y-%m-%d")
            hist = df[df.index <= td_str]
            if len(hist) < window:
                continue
            close = hist["close"]
            ma = close.iloc[-window:].mean()
            std = close.iloc[-window:].std()
            upper = ma + width * std
            lower = ma - width * std
            last = close.iloc[-1]

            if last <= lower:
                signal = "buy"
            elif last >= upper:
                signal = "sell"
            else:
                signal = "watch"

            # Only surface actionable signals to keep the UI focused
            if signal in ("buy", "sell"):
                score = round(s.composite_score, 4)
                confidence = round(min(0.99, abs(last - ma) / (std + 1e-9) / width), 4)
                meta = {
                    "score": score,
                    "confidence": confidence,
                    "ma20": round(float(ma), 2),
                    "upper": round(float(upper), 2),
                    "lower": round(float(lower), 2),
                    "close": round(float(last), 2),
                }
                meta["action_suggestion"] = _build_action_suggestion(
                    "bollinger_band", signal, meta
                )
                matched.append(
                    MatchedSymbol(
                        symbol=s.symbol,
                        name=s.name or get_stock_name(s.symbol),
                        signal=signal,
                        score=score,
                        confidence=confidence,
                        rank=idx,
                        metadata=meta,
                    )
                )

        return StrategySnapshot(
            strategy_id=request.strategy_id,
            run_at=datetime.now(),
            status="success",
            market_date=market_date,
            matched=matched,
        )
    except Exception as exc:
        logger.exception("band run failed")
        return _empty_snapshot(request.strategy_id, market_date, "error", str(exc))


# --------------------------------------------------------------------------- #
# Runner: adaptive (per-stock personality)
# --------------------------------------------------------------------------- #


def _run_adaptive(request: StrategyRunRequest) -> StrategySnapshot:
    market_date = _market_date(request, _data_root(request))
    top_n = _int_param(request.params, "top_n", 20)
    try:
        start, end = _backtest_range(market_date)
        bt = AdaptiveBacktest(data_root=_data_root(request))
        universe = _universe_param(request.params) or ["all_a"]
        bt.preload_data(start_date=start, end_date=end, universe=universe)
        result = bt.run(
            start_date=start,
            end_date=end,
            top_n=top_n,
            max_positions=_int_param(request.params, "max_positions", 10),
        )

        # Today's buy candidates from adaptive selection
        matched: list[MatchedSymbol] = []
        candidates = bt._select_stocks(market_date, top_n)
        for idx, c in enumerate(candidates, start=1):
            score = c.get("score")
            score_val = round(float(score), 4) if score is not None else None
            confidence = round(min(0.99, abs(float(score)) / 100.0 if score else 0.0), 4)
            momentum_20d = round(float(c.get("momentum_20d", 0)), 2)
            volume_ratio = round(float(c.get("volume_ratio", 0)), 2)
            meta: dict[str, Any] = {
                "score": score_val,
                "confidence": confidence,
                "momentum_20d": momentum_20d,
                "volume_ratio": volume_ratio,
                "ma5": round(float(c.get("ma5", 0)), 2),
            }
            meta["action_suggestion"] = _build_action_suggestion(
                "adaptive_personality", "buy", meta
            )
            matched.append(
                MatchedSymbol(
                    symbol=c["symbol"],
                    name=get_stock_name(c["symbol"]),
                    signal="buy",
                    score=score_val,
                    confidence=confidence,
                    rank=idx,
                    metadata=meta,
                )
            )

        curve = [
            {
                "date": e["date"],
                "value": round(e["total_value"], 2),
                "drawdown_pct": round(e["drawdown_pct"], 2),
            }
            for e in result.equity_curve
        ]

        return StrategySnapshot(
            strategy_id=request.strategy_id,
            run_at=datetime.now(),
            status="success",
            market_date=market_date,
            matched=matched,
            metrics=_result_to_metrics(result),
            backtest_curve=curve,
        )
    except Exception as exc:
        logger.exception("adaptive run failed")
        return _empty_snapshot(request.strategy_id, market_date, "error", str(exc))


# --------------------------------------------------------------------------- #
# Runner: stock profile
# --------------------------------------------------------------------------- #


def _run_profile(request: StrategyRunRequest) -> StrategySnapshot:
    market_date = _market_date(request, _data_root(request))
    symbol = str(request.params.get("symbol", "")).strip()
    if not symbol:
        return _empty_snapshot(
            request.strategy_id, market_date, "error", "missing 'symbol' parameter"
        )
    try:
        loader = LocalKlineLoader(_data_root(request))
        begin = (market_date - timedelta(days=180)).strftime("%Y%m%d")
        end = market_date.strftime("%Y%m%d")
        df = loader.load(symbol, begin, end)
        if df is None:
            return _empty_snapshot(
                request.strategy_id, market_date, "error", f"no data for {symbol}"
            )
        profile = StockProfile.from_bars(df, symbol=symbol)
        from src.ashare.strategies.adaptive_risk import BandParams

        params = BandParams.from_profile(profile)

        return StrategySnapshot(
            strategy_id=request.strategy_id,
            run_at=datetime.now(),
            status="success",
            market_date=market_date,
            matched=[
                MatchedSymbol(
                    symbol=symbol,
                    name=get_stock_name(symbol),
                    signal="watch",
                    score=profile.hv_20,
                    confidence=round(min(0.99, profile.adx_14 / 50.0), 4),
                    rank=1,
                    metadata={
                        "profile": profile.to_dict(),
                        "adaptive_params": params.to_dict(),
                    },
                )
            ],
        )
    except Exception as exc:
        logger.exception("profile run failed")
        return _empty_snapshot(request.strategy_id, market_date, "error", str(exc))


# --------------------------------------------------------------------------- #
# Strategy catalogue
# --------------------------------------------------------------------------- #

SELECTOR_DEF = StrategyDefinition(
    id="local_selector",
    name="多因子选股",
    description="基于动量、成交量、趋势强度的综合打分模型，从全 A 股中每日筛选强势股。",
    category=StrategyCategory.SELECTOR,
    params=[
        StrategyParam(
            id="top_n",
            name="选股数量",
            type="int",
            default=20,
            min=5,
            max=100,
            description="返回排名靠前的股票数量",
        ),
        StrategyParam(
            id="data_root",
            name="数据根目录",
            type="str",
            default="",
            description="本地 parquet 数据目录（留空自动检测）",
        ),
    ],
)

TIMING_DEF = StrategyDefinition(
    id="trend_timing",
    name="趋势择时",
    description="从全 A 股中筛选强动量、强趋势、放量突破的强势股，生成买入信号。",
    category=StrategyCategory.TIMING,
    params=[
        StrategyParam(
            id="top_n",
            name="选股数量",
            type="int",
            default=20,
            min=5,
            max=100,
            description="候选池大小",
        ),
    ],
)

BAND_DEF = StrategyDefinition(
    id="bollinger_band",
    name="布林带波段",
    description="从全 A 股中筛选短期超跌但长期趋势仍在的均值回复候选，再基于布林带上下轨生成高抛低吸信号。",
    category=StrategyCategory.BAND,
    params=[
        StrategyParam(
            id="top_n",
            name="候选池大小",
            type="int",
            default=60,
            min=10,
            max=200,
            description="用于计算 band 信号的候选股票数",
        ),
        StrategyParam(
            id="band_window",
            name="布林带窗口",
            type="int",
            default=20,
            min=5,
            max=60,
            description="布林带均线窗口",
        ),
        StrategyParam(
            id="band_width",
            name="带宽倍数",
            type="float",
            default=2.0,
            min=0.5,
            max=4.0,
            description="标准差倍数",
        ),
    ],
)

ADAPTIVE_DEF = StrategyDefinition(
    id="adaptive_personality",
    name="自适应个性策略",
    description="从全 A 股中根据每只股票的历史股性（波动、趋势、均值回归）动态选股，并自适应调整止损止盈与仓位。",
    category=StrategyCategory.ADAPTIVE,
    params=[
        StrategyParam(
            id="top_n",
            name="选股数量",
            type="int",
            default=20,
            min=5,
            max=100,
        ),
        StrategyParam(
            id="max_positions",
            name="最大持仓",
            type="int",
            default=10,
            min=1,
            max=50,
        ),
    ],
)

PROFILE_DEF = StrategyDefinition(
    id="stock_profile",
    name="个股画像",
    description="分析单只股票的股性特征并输出自适应交易参数。",
    category=StrategyCategory.PROFILE,
    params=[
        StrategyParam(
            id="symbol",
            name="股票代码",
            type="str",
            default="000001.SZ",
            description="带后缀的股票代码，例如 000001.SZ",
        ),
    ],
    supports_backtest=False,
    market_visible=False,  # 需要用户输入指定 symbol，不适合策略市场批量刷新
)

register_strategy(SELECTOR_DEF)(_run_selector)
register_strategy(TIMING_DEF)(_run_timing)
register_strategy(BAND_DEF)(_run_band)
register_strategy(ADAPTIVE_DEF)(_run_adaptive)
register_strategy(PROFILE_DEF)(_run_profile)


# --------------------------------------------------------------------------- #
# User-defined strategies (signal-delivery)                                  #
# --------------------------------------------------------------------------- #
# These strategies emit signals to LocalSink / SSESink instead of the backtest
# flow used by the built-in runners above. See docs/superpowers/specs/
# 2026-06-24-signal-delivery-design.md §4.2 and §4.3 for the design.

from src.ashare.strategies.my_multi_factor import MYF_DEF, run_myf  # noqa: E402
from src.ashare.strategies.my_bollinger import BOLL_DEF, run_boll  # noqa: E402

register_strategy(MYF_DEF)(run_myf)
register_strategy(BOLL_DEF)(run_boll)
