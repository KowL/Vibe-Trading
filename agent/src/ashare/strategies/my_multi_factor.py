"""用户多因子选股策略（动量 + 反转 + 量比 综合排名）。

设计思路（设计文档 §0.3，§4.2）：
    每日盘后 16:30-17:00 由调度器触发（scheduler 中 my_multi_factor_eod job）。
    一次性扫描整个 watchlist，按综合分排序，输出 top N 买入候选 + bottom N 减仓候选。

综合分构成（默认权重，可在 StrategyDefinition.params 里调）：
    - 20日动量 (ret_20)     权重 0.40
    - 5日反转 (-ret_5)      权重 0.20  短期回调 = 买入机会
    - 量比     (vol_ratio)  权重 0.20  5日均量 / 20日均量
    - 20日波动 (-std_20)    权重 0.20  低波动偏好（控制回撤）

过滤规则（在打分前剔除不合格标的）：
    - 上市不足 60 个交易日 → 跳过
    - 收盘价 < MA20 → 跳过（不在下降趋势里买）
    - 20日动量 < 0 → 跳过（半年趋势向下不参与）
    - 量比 < 0.8 → 跳过（缩量无资金关注）

数据源：TushareClient（本地 adshare /dataapi 兼容层或真实 tushare cloud），不依赖本地 parquet。
失败处理：单只票拉取失败 → WARNING + 跳过，不影响其他。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.ashare.tushare_client import TushareClient
from src.ashare.signals.delivery import get_delivery_service
from src.ashare.strategies.market_models import (
    MatchedSymbol,
    StrategyCategory,
    StrategyDefinition,
    StrategyMetrics,
    StrategyRunRequest,
    StrategySnapshot,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #

WATCHLIST_PATH = "~/.vibe-trading/ashare/my_universe.txt"

DEFAULT_PARAMS: dict[str, Any] = {
    "top_n": 10,                 # 买入候选数
    "bottom_n": 10,              # 减仓候选数（用于反向信号，非做空）
    "mom_window": 20,            # 动量窗口（自然日）
    "reversal_window": 5,        # 反转窗口
    "vol_window_short": 5,       # 量比短窗口
    "vol_window_long": 20,       # 量比长窗口
    "w_mom": 0.40,               # 动量权重
    "w_rev": 0.20,               # 反转权重
    "w_vol": 0.20,               # 量比权重
    "w_inv_vol": 0.20,           # 低波动权重
    "min_mom_20": 0.0,           # 20日动量下限（低于则过滤）
    "min_volume_ratio": 0.8,     # 量比下限
    "min_history_bars": 60,      # 最小历史 K 线数
    "request_sleep": 0.05,       # 数据源限流间隔（秒）
    "lookback_days": 120,        # 拉数据回看天数（覆盖节假日保证 ≥60 根有效 K 线）
}


MYF_DEF = StrategyDefinition(
    id="my_multi_factor",
    name="我的多因子选股",
    description=(
        "20日动量 + 5日反转 + 量比 + 低波动 综合排名，"
        "每日盘后输出 top N 买入候选 + bottom N 减仓候选。"
    ),
    category=StrategyCategory.SELECTOR,
    params=[
        # 简化版：把核心可调参数透出即可；用 dict 默认值保持简洁
    ],
    supports_backtest=True,
    supports_realtime=False,  # 这是 EOD 策略，不做盘中实时
    market_visible=False,  # 信号投递策略，不显示在策略市场
)


# --------------------------------------------------------------------------- #
# Watchlist loader                                                            #
# --------------------------------------------------------------------------- #


def _load_watchlist() -> list[str]:
    """Load the user's watchlist from disk.

    Format: one ``XXXXXX.SH`` / ``XXXXXX.SZ`` per line, ``#`` 开头的行为注释。
    Missing file → empty list（runner 会返回 status=success, matched=[]）。
    """
    p = Path(WATCHLIST_PATH).expanduser()
    if not p.exists():
        logger.warning("multi_factor watchlist missing: %s", p)
        return []
    return [
        line.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


# --------------------------------------------------------------------------- #
# Data helpers                                                                #
# --------------------------------------------------------------------------- #


def _bars_to_df(bars: list[dict[str, Any]]):
    """Convert the tushare/adshare ``data`` list of dicts to a pandas DataFrame sorted by date.

    Returns ``None`` on bad input; the runner skips that symbol. The
    DataFrame is indexed by a tz-naive :class:`datetime.date` so
    downstream rolling computations line up with a single time axis.
    """
    if not bars:
        return None
    import pandas as pd

    try:
        df = pd.DataFrame(bars)
    except (ValueError, TypeError) as exc:
        logger.warning("multi_factor: bad bars shape: %s", exc)
        return None
    # ``date`` is YYYYMMDD as int; coerce to datetime.date for stable indexing.
    df["date"] = pd.to_datetime(df["date"].astype(int).astype(str), format="%Y%m%d")
    df = df.sort_values("date").reset_index(drop=True)
    df = df.set_index("date")
    return df


def _score_one(df, params: dict[str, Any]) -> tuple[float, dict[str, Any]] | None:
    """Compute the composite score for one symbol's price history.

    Returns ``(score, components)`` or ``None`` if the symbol does not
    pass the filters (insufficient history, MA20 downtrend, negative
    20-day momentum, or volume too thin).
    """
    close = df["close"]
    volume = df["volume"]

    mom_w = int(params["mom_window"])
    rev_w = int(params["reversal_window"])
    vs = int(params["vol_window_short"])
    vl = int(params["vol_window_long"])
    min_bars = int(params["min_history_bars"])
    if len(close) < max(min_bars, mom_w, vl) + 1:
        return None

    # 20日动量（%）
    ret_mom = float(close.iloc[-1] / close.iloc[-mom_w] - 1.0)
    # 5日反转（%）：短期回调 = 正分
    ret_rev = float(close.iloc[-1] / close.iloc[-rev_w] - 1.0)
    # 20日波动（年化前先 std）
    std_20 = float(close.pct_change().rolling(mom_w).std().iloc[-1])
    # 量比 = 5日均量 / 20日均量
    vol_ma_s = float(volume.rolling(vs).mean().iloc[-1])
    vol_ma_l = float(volume.rolling(vl).mean().iloc[-1])
    vol_ratio = vol_ma_s / vol_ma_l if vol_ma_l > 0 else 0.0
    # 趋势过滤：MA20 与收盘价的关系
    ma20 = float(close.rolling(mom_w).mean().iloc[-1])

    # 过滤
    if ret_mom < float(params["min_mom_20"]):
        return None
    if vol_ratio < float(params["min_volume_ratio"]):
        return None
    if close.iloc[-1] < ma20:
        return None

    # 综合分
    score = (
        ret_mom * float(params["w_mom"])
        + (-ret_rev) * float(params["w_rev"])
        + (vol_ratio - 1.0) * float(params["w_vol"])
        + (-std_20) * float(params["w_inv_vol"])
    )
    components = {
        "ret_20": round(ret_mom, 4),
        "ret_5": round(ret_rev, 4),
        "vol_ratio": round(vol_ratio, 3),
        "std_20": round(std_20, 4),
        "ma20": round(ma20, 3),
        "last_close": round(float(close.iloc[-1]), 3),
    }
    return score, components


# --------------------------------------------------------------------------- #
# Runner                                                                      #
# --------------------------------------------------------------------------- #


def run_myf(request: StrategyRunRequest) -> StrategySnapshot:
    """Scan the watchlist and return a :class:`StrategySnapshot` of top + bottom picks.

    Delivery: this runner does NOT call the signal delivery service
    directly. The top N are delivered by the scheduler
    (``AShareTaskRunner._run("my_multi_factor_eod")``) which iterates
    over ``snapshot.matched`` and pushes each. Keeping delivery in the
    scheduler means the runner stays a pure function (input → snapshot),
    which is what ``StrategyMarketEngine.refresh`` already assumes.
    """
    import time as _time

    market_date = request.market_date or date.today()
    params = {**DEFAULT_PARAMS, **(request.params or {})}
    top_n = int(params["top_n"])
    bottom_n = int(params["bottom_n"])
    sleep_s = float(params["request_sleep"])
    lookback_days = int(params["lookback_days"])

    watchlist = _load_watchlist()
    if not watchlist:
        logger.warning("my_multi_factor: watchlist empty; returning empty snapshot")
        return StrategySnapshot(
            strategy_id="my_multi_factor", run_at=datetime.now(),
            status="success", market_date=market_date, matched=[],
            metrics=StrategyMetrics(),
            metadata={"empty_reason": "watchlist_empty", "watchlist_path": str(Path(WATCHLIST_PATH).expanduser())},
        )

    client = TushareClient()
    end_date = market_date.strftime("%Y%m%d")
    begin_date = (market_date - timedelta(days=lookback_days)).strftime("%Y%m%d")

    scored: list[tuple[str, float, dict[str, Any]]] = []
    for symbol in watchlist:
        try:
            resp = client.get_kline(
                symbol, period="daily",
                begin_date=begin_date, end_date=end_date,
            )
        except Exception as exc:
            logger.warning("my_multi_factor: %s fetch failed: %s", symbol, exc)
            _time.sleep(sleep_s)
            continue
        bars = (resp or {}).get("data") or []
        df = _bars_to_df(bars)
        if df is None:
            _time.sleep(sleep_s)
            continue
        scored_one = _score_one(df, params)
        if scored_one is None:
            _time.sleep(sleep_s)
            continue
        score, components = scored_one
        scored.append((symbol, score, components))
        _time.sleep(sleep_s)

    if not scored:
        logger.warning("my_multi_factor: no symbols passed filters on %s", market_date)
        return StrategySnapshot(
            strategy_id="my_multi_factor", run_at=datetime.now(),
            status="success", market_date=market_date, matched=[],
            metrics=StrategyMetrics(),
            metadata={
                "empty_reason": "no_symbols_passed_filters",
                "watchlist_size": len(watchlist),
                "market_date": market_date.isoformat(),
                "begin_date": begin_date,
                "end_date": end_date,
            },
        )

    scored.sort(key=lambda t: t[1], reverse=True)
    top = scored[:top_n]
    bottom = scored[-bottom_n:] if bottom_n > 0 else []

    # top：买入候选（buy）
    matched: list[MatchedSymbol] = [
        MatchedSymbol(
            symbol=symbol, signal="buy",
            score=round(float(score), 4),
            confidence=round(min(0.99, max(0.0, abs(score) * 5)), 4),
            rank=idx + 1,
            metadata={**components, "decision": "buy"},
        )
        for idx, (symbol, score, components) in enumerate(top)
    ]
    # bottom：减仓候选（watch，不直接 sell）
    # 用 rank 字段错开（top 用 1..N，bottom 用 -1..-N）
    for idx, (symbol, score, components) in enumerate(bottom, start=1):
        matched.append(MatchedSymbol(
            symbol=symbol, signal="watch",
            score=round(float(score), 4),
            confidence=round(min(0.99, abs(score) * 5), 4),
            rank=-idx,
            metadata={**components, "decision": "trim"},
        ))

    return StrategySnapshot(
        strategy_id="my_multi_factor", run_at=datetime.now(),
        status="success", market_date=market_date, matched=matched,
    )
