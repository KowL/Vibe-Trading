"""用户布林带波段策略（多标的版）。

设计思路（设计文档 §0.3，§4.3，D6 ✅ 2026-06-25 拍板）：
    盘中每 N 分钟（默认 5 分钟）由调度器触发（scheduler 中 my_bollinger_scan job）。
    一次性扫描整个 watchlist，每只命中的标的单独发一条信号。
    4 种触发（SPEC §4.3 注释 + wanrun_band 风格）：

        突破上轨 → sell       (上轨压力)
        跌破下轨 → buy        (下轨支撑反转)
        从上轨回归中轨 → hold (获利了结)
        从下轨回归中轨 → hold (空头回补)

数据源：adshare ``/realtime/kline?codes=...&period=1``。
    盘中（9:30-15:00）有 1m K 缓存，盘后接口仍 200 但 data=[]，runner 自动跳过。
    限流：watchlist 50-200 只 × 0.3s = 单次扫描 < 60s，符合 5 分钟调度间隔。

去重：dedup key = (strategy_id, symbol, side) 跨 watchlist 共享 30 分钟冷却。
    同一标的同方向 30 分钟内只推一次，避免盘整时反复触发布林带。

失败：单只票拉取失败 → WARNING + 跳过；watchlist 为空 → 返回空 snapshot，status=success。
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from src.ashare.signals.delivery import get_delivery_service
from src.ashare.strategies.market_models import (
    MatchedSymbol,
    StrategyCategory,
    StrategyDefinition,
    StrategyRunRequest,
    StrategySnapshot,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #

WATCHLIST_PATH = "~/.vibe-trading/ashare/bollinger_watchlist.txt"
ADSHARE_BASE = "http://localhost:8000"
REALTIME_KLINE_PATH = "/realtime/kline"

DEFAULT_PARAMS: dict[str, Any] = {
    "period": 20,          # 布林带均线窗口（根数）
    "std_n": 2.0,          # 标准差倍数
    "min_bars": 25,        # 触发所需的最少 K 线数（含 period + 5 缓冲）
    "request_sleep": 0.3,  # adshare 限流间隔（秒）
    "cooldown_seconds": 1800,  # 同 (symbol, side) 30 分钟内不重复推
    "lookback_limit": 240,     # 拉多少根 1m K（4 小时，足够一天盘内）
}


BOLL_DEF = StrategyDefinition(
    id="my_bollinger",
    name="我的布林带波段",
    description=(
        "20周期布林带（默认 2σ），盘中每 5 分钟扫描 watchlist，"
        "突破上轨/跌破下轨/回归中轨各触发一次信号。"
    ),
    category=StrategyCategory.BAND,
    params=[],
    supports_backtest=False,  # 盘中策略，不做历史回测
    supports_realtime=True,
    market_visible=False,  # 信号投递策略，不显示在策略市场
)


# --------------------------------------------------------------------------- #
# Watchlist loader                                                            #
# --------------------------------------------------------------------------- #


def _load_watchlist() -> list[str]:
    """Load the watchlist from disk; missing file → empty list."""
    p = Path(WATCHLIST_PATH).expanduser()
    if not p.exists():
        logger.warning("bollinger watchlist missing: %s", p)
        return []
    return [
        line.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


# --------------------------------------------------------------------------- #
# Realtime data fetcher                                                       #
# --------------------------------------------------------------------------- #


def _fetch_realtime_kline(
    code: str, period: str, limit: int, base_url: str = ADSHARE_BASE
) -> list[dict[str, Any]]:
    """Call ``GET /realtime/kline?codes=<code>&period=<period>``.

    Returns the raw ``data`` list (bars). Empty list means: 200 OK
    but no data cached (typically: outside trading hours). Network
    errors raise; the runner catches them and skips the symbol.
    """
    url = f"{base_url.rstrip('/')}{REALTIME_KLINE_PATH}"
    with httpx.Client(timeout=5.0) as client:
        r = client.get(url, params={"codes": code, "period": period})
        r.raise_for_status()
        payload = r.json()
    return list(payload.get("data") or [])


def _bars_to_close_series(bars: list[dict[str, Any]]):
    """Project a list of bar dicts to a ``pd.Series`` of close prices.

    Adshare realtime K uses a numeric ``time`` (epoch ms or YYYYMMDDHHmm).
    We only need the close series, not the index, so we just return it.
    """
    if not bars:
        return None
    try:
        closes = [float(b["close"]) for b in bars]
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("bollinger: malformed bar in response: %s", exc)
        return None
    return pd.Series(closes)


# --------------------------------------------------------------------------- #
# Signal detection                                                            #
# --------------------------------------------------------------------------- #


def _detect_cross(
    close: pd.Series, period: int, std_n: float
) -> dict[str, Any] | None:
    """Detect a single-event cross on the last bar vs. the prior bar.

    Returns a dict with ``side`` / ``ref_price`` / ``trigger`` / metadata
    if the last bar crosses a Bollinger band that the prior bar did not
    (i.e. the *first* bar of a regime change). Returns ``None`` if no
    cross is detected or the series is too short.
    """
    if close is None or len(close) < period + 2:
        return None
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    upper = mid + std_n * std
    lower = mid - std_n * std

    c = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    u, m, lo = float(upper.iloc[-1]), float(mid.iloc[-1]), float(lower.iloc[-1])

    # 上穿下轨 → buy
    if prev >= lo and c < lo:
        return {
            "side": "buy", "ref_price": c, "trigger": "break_lower",
            "metadata": {
                "band_upper": round(u, 3), "band_mid": round(m, 3),
                "band_lower": round(lo, 3),
                "prev_close": round(prev, 3), "period": period, "std_n": std_n,
            },
        }
    # 下穿上轨 → sell
    if prev <= u and c > u:
        return {
            "side": "sell", "ref_price": c, "trigger": "break_upper",
            "metadata": {
                "band_upper": round(u, 3), "band_mid": round(m, 3),
                "band_lower": round(lo, 3),
                "prev_close": round(prev, 3), "period": period, "std_n": std_n,
            },
        }
    # 从上轨回到中轨 → hold（获利了结）
    if prev > u and c <= m:
        return {
            "side": "hold", "ref_price": c, "trigger": "revert_from_upper",
            "metadata": {
                "band_upper": round(u, 3), "band_mid": round(m, 3),
                "band_lower": round(lo, 3),
                "prev_close": round(prev, 3), "period": period, "std_n": std_n,
            },
        }
    # 从下轨回到中轨 → hold（空头回补 / 反转确认）
    if prev < lo and c >= m:
        return {
            "side": "hold", "ref_price": c, "trigger": "revert_from_lower",
            "metadata": {
                "band_upper": round(u, 3), "band_mid": round(m, 3),
                "band_lower": round(lo, 3),
                "prev_close": round(prev, 3), "period": period, "std_n": std_n,
            },
        }
    return None


# --------------------------------------------------------------------------- #
# Runner                                                                      #
# --------------------------------------------------------------------------- #


def run_boll(request: StrategyRunRequest) -> StrategySnapshot:
    """Scan the watchlist and return a :class:`StrategySnapshot` of all hits.

    Delivery: this runner returns a snapshot containing every hit and
    *also* fires a per-signal :func:`SignalDeliveryService.deliver_for_symbol`
    via ``asyncio.create_task`` so the SSE bus and local file get the
    signals without waiting for the scheduler to round-trip. The
    scheduler's ``my_bollinger_scan`` job still awaits the runner, so
    any in-flight tasks run before the next tick fires.
    """
    market_date = request.market_date or date.today()
    params = {**DEFAULT_PARAMS, **(request.params or {})}
    period = int(params["period"])
    std_n = float(params["std_n"])
    min_bars = int(params["min_bars"])
    sleep_s = float(params["request_sleep"])
    lookback = int(params["lookback_limit"])
    period_str = "1"  # 1-minute K is the project's default intraday period

    watchlist = _load_watchlist()
    if not watchlist:
        logger.warning("my_bollinger: watchlist empty; returning empty snapshot")
        return StrategySnapshot(
            strategy_id="my_bollinger", run_at=datetime.now(),
            status="success", market_date=market_date, matched=[],
        )

    matched: list[MatchedSymbol] = []
    now_ts = datetime.now()
    for symbol in watchlist:
        try:
            bars = _fetch_realtime_kline(symbol, period_str, lookback)
        except Exception as exc:
            logger.warning("my_bollinger: %s fetch failed: %s", symbol, exc)
            _time.sleep(sleep_s)
            continue
        if not bars or len(bars) < min_bars:
            _time.sleep(sleep_s)
            continue
        close = _bars_to_close_series(bars)
        if close is None:
            _time.sleep(sleep_s)
            continue
        sig = _detect_cross(close, period, std_n)
        if sig is None:
            _time.sleep(sleep_s)
            continue

        # MatchedSymbol in HEAD does not have a top-level ``ref_price``
        # field; the price is carried under ``metadata`` so consumers
        # (LocalSink / SSESink / delivery service) can read it from
        # one consistent place.
        m = MatchedSymbol(
            symbol=symbol, signal=sig["side"],
            confidence=0.8,
            metadata={
                **sig["metadata"],
                "ref_price": sig["ref_price"],
                "trigger": sig["trigger"],
                "ts": now_ts.isoformat(timespec="seconds"),
            },
        )
        matched.append(m)

        # 异步投递：每只命中的标的单独发一条信号
        # 不在 run_boll 主路径等推送完成（run_boll 是同步函数，
        # 拿不到事件循环的 await；create_task 把投递挂到事件循环上）
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                delivery = get_delivery_service()
                loop.create_task(delivery.deliver_for_symbol(
                    strategy_id="my_bollinger", market_date=market_date,
                    symbol=symbol, side=sig["side"], ref_price=sig["ref_price"],
                    confidence=0.8, reason=sig["trigger"],
                    metadata=sig["metadata"],
                ))
        except RuntimeError:
            # 没有事件循环（runner 被同步调用）→ 跳过本次投递，
            # scheduler 在 engine.refresh 后可以从 snapshot 投递
            pass

        _time.sleep(sleep_s)

    if not matched:
        logger.debug("my_bollinger: no cross detected for %d symbols", len(watchlist))

    return StrategySnapshot(
        strategy_id="my_bollinger", run_at=datetime.now(),
        status="success", market_date=market_date, matched=matched,
    )
