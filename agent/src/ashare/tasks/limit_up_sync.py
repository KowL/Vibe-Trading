"""A-share limit-up data sync task.

Ports Ruo.ai's 15:30 涨停同步任务 to Vibe-Trading's asyncio scheduler.
Fetches the day's limit-up board from akshare (primary, free / East Money),
with adshare/tushare fallbacks, parses into LimitUpDaily records, and
persists to ~/.vibe-trading/ashare/limit_up/.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Mapping

import httpx

from src.ashare.models.limit_up import LimitUpDaily
from src.ashare.storage.limit_up_store import LimitUpStore
from src.ashare.tushare_client import TushareClient

logger = logging.getLogger(__name__)

_ADSHARE_BASE = "http://localhost:8000"


@dataclass
class LimitUpSyncResult:
    """Result of a single sync run."""

    trade_date: date
    count: int
    source: str
    errors: list[str]


def _today_shanghai() -> date:
    """Return today's date in Asia/Shanghai timezone."""
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _normalize_symbol(raw: str) -> str:
    """Normalize symbol to Vibe-Trading style (e.g. 000001.SZ)."""
    raw = str(raw).strip().upper()
    if "." in raw:
        return raw
    # Heuristic: 6-digit codes starting with 6 are SH, otherwise SZ
    if raw.startswith("6"):
        return f"{raw}.SH"
    return f"{raw}.SZ"


def _parse_time(value: Any) -> time | None:
    """Parse '09:30:00' / '09:30' / '093000' (HHMMSS) into a time object."""
    if value is None or value == "":
        return None
    s = str(value).strip()
    # AkShare 东方财富 returns HHMMSS without colons, e.g. '092500'
    if len(s) == 6 and s.isdigit():
        try:
            return time(int(s[:2]), int(s[2:4]), int(s[4:6]))
        except ValueError:
            return None
    # ISO-style '09:30:00' or '09:30'
    if len(s) >= 5:
        try:
            return time.fromisoformat(s[:8])
        except ValueError:
            pass
    return None


def _adshare_limit_up(trade_date: date) -> list[LimitUpDaily]:
    """Fetch limit-up board from tushare/adshare /market/limit-up."""
    client = TushareClient()
    try:
        payload = client.get_limit_up(date=trade_date.strftime("%Y%m%d"), days=1)
        rows = payload.get("stocks", []) if isinstance(payload, dict) else []
        records: list[LimitUpDaily] = []
        for row in rows:
            symbol = _normalize_symbol(row.get("code") or row.get("symbol", ""))
            if not symbol or symbol == ".SH" or symbol == ".SZ":
                continue
            rec = LimitUpDaily(
                trade_date=trade_date,
                symbol=symbol,
                name=row.get("name", ""),
                limit_up_count=int(row.get("limitUpDays") or row.get("limit_up_count") or 1),
                limit_up_price=_float(row.get("price") or row.get("limit_up_price")),
                open_price=_float(row.get("open") or row.get("open_price")),
                close_price=_float(row.get("price") or row.get("close_price")),
                high_price=_float(row.get("high") or row.get("high_price")),
                low_price=_float(row.get("low") or row.get("low_price")),
                prev_close=_float(row.get("preClose") or row.get("prev_close")),
                change_pct=_float(row.get("changePct") or row.get("change_pct")),
                turnover_amount=_float(row.get("amount") or row.get("turnover_amount")),
                turnover_volume=_float(row.get("volume") or row.get("turnover_volume")),
                turnover_ratio=_float(row.get("turnover") or row.get("turnover_ratio")),
                seal_amount=_float(row.get("sealAmount") or row.get("seal_amount")),
                seal_ratio=_float(row.get("sealRatio") or row.get("seal_ratio")),
                first_time=_parse_time(row.get("firstTime") or row.get("first_time")),
                last_time=_parse_time(row.get("finalTime") or row.get("last_time")),
                open_count=int(row.get("openCount") or row.get("open_count") or 0) if (row.get("openCount") or row.get("open_count")) else None,
                industry=row.get("industry") or None,
                concept=row.get("concept") or None,
                reason=row.get("reason") or None,
                source="tushare/adshare",
            )
            records.append(rec)
        return records
    finally:
        client.close()


def _float(value: Any) -> float:
    """Safe float cast; returns 0.0 for None/empty."""
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _fallback_tushare(trade_date: date) -> list[LimitUpDaily]:
    """Placeholder: tushare kpl_list / limit_list_d fallback."""
    # Import is lazy so tushare is not a hard dependency.
    try:
        import tushare as ts
    except ImportError:
        return []
    token = _tushare_token()
    if not token:
        return []
    pro = ts.pro_api(token)
    df = pro.limit_list_d(trade_date=trade_date.strftime("%Y%m%d"))
    records: list[LimitUpDaily] = []
    for _, row in df.iterrows():
        symbol = _normalize_symbol(row.get("ts_code", ""))
        if not symbol:
            continue
        records.append(
            LimitUpDaily(
                trade_date=trade_date,
                symbol=symbol,
                name=row.get("name", ""),
                limit_up_count=int(row.get("limit_times") or 1),
                close_price=_float(row.get("close")),
                change_pct=_float(row.get("pct_chg")),
                turnover_amount=_float(row.get("amount")) * 1000,
                turnover_ratio=_float(row.get("turnover_ratio")),
                first_time=_parse_time(row.get("first_time")),
                source="tushare",
            )
        )
    return records


def _akshare_limit_up(trade_date: date) -> list[LimitUpDaily]:
    """Fetch limit-up board from AkShare (东方财富).

    Combines the sealed pool (stock_zt_pool_em) and the broken-board pool
    (stock_zt_pool_zbgc_em) so 炸板 records also flow through. AkShare is
    free and unauthenticated; columns are Chinese.

    Field coverage:
        sealed 池: 连板数, 封板资金, 首次/最后封板时间, 炸板次数
        broken 池: 涨停价, 首次封板时间, 炸板次数
    Merging the two gives near-complete data for the UI.
    """
    try:
        import akshare as ak
    except ImportError:
        return []

    date_str = trade_date.strftime("%Y%m%d")
    records: list[LimitUpDaily] = []
    seen: set[str] = set()

    pools: list[tuple[object, bool]] = []
    try:
        pools.append((ak.stock_zt_pool_em(date=date_str), True))
    except Exception as exc:
        logger.warning("akshare sealed pool failed: %s", exc)
    try:
        pools.append((ak.stock_zt_pool_zbgc_em(date=date_str), False))
    except Exception as exc:
        logger.warning("akshare broken pool failed: %s", exc)

    for df, sealed in pools:
        if df is None or len(df) == 0:
            continue
        for _, row in df.iterrows():
            symbol = _normalize_symbol(row.get("代码", ""))
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)

            change_pct_raw = _float(row.get("涨跌幅"))          # 百分比数值
            turnover_ratio_raw = _float(row.get("换手率"))      # 百分比数值
            close_price = _float(row.get("最新价"))
            # AkShare 东方财富：成交额 / 封板资金 均为元（不是万元）
            turnover_amount = _float(row.get("成交额")) or 0.0
            seal_amount_raw = _float(row.get("封板资金"))
            seal_amount = seal_amount_raw if seal_amount_raw > 0 else None
            limit_up_price = _float(row.get("涨停价")) or 0.0   # 炸板池才有
            first_time = _parse_time(row.get("首次封板时间"))
            last_time = _parse_time(row.get("最后封板时间"))
            open_count = int(_float(row.get("炸板次数")) or 0) or None

            # 涨停价 fallback：封板池无此字段，用 close 顶替（is_sealed=True 自然成立）
            if not limit_up_price:
                limit_up_price = close_price

            # 连板数：封板池直接有 "连板数"；炸板池无此字段，从 "涨停统计" 解析
            limit_count_raw = row.get("连板数")
            if limit_count_raw is not None and str(limit_count_raw).strip() != "":
                limit_up_count = int(_float(limit_count_raw) or 1)
            else:
                stat = str(row.get("涨停统计", ""))
                # 形如 "3天3板"、"首板" 等。优先取 "N板" 中的 N；否则取最后一个数字
                import re
                m = re.search(r"(\d+)\s*板", stat)
                if m:
                    limit_up_count = int(m.group(1))
                else:
                    nums = re.findall(r"\d+", stat)
                    limit_up_count = int(nums[-1]) if nums else 1

            records.append(
                LimitUpDaily(
                    trade_date=trade_date,
                    symbol=symbol,
                    name=row.get("名称", ""),
                    limit_up_count=limit_up_count,
                    limit_up_price=limit_up_price,
                    close_price=close_price,
                    change_pct=change_pct_raw / 100 if change_pct_raw else 0.0,
                    turnover_amount=turnover_amount,
                    turnover_ratio=turnover_ratio_raw / 100 if turnover_ratio_raw else 0.0,
                    seal_amount=seal_amount,
                    first_time=first_time,
                    last_time=last_time,
                    open_count=open_count,
                    industry=row.get("所属行业") or None,
                    source="akshare",
                )
            )
    return records


def _tushare_token() -> str | None:
    """Read tushare token from env or ~/.vibe-trading/.env."""
    import os

    token = os.environ.get("TUSHARE_TOKEN")
    if token:
        return token
    env_path = Path.home() / ".vibe-trading" / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("TUSHARE_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


class LimitUpSyncTask:
    """Sync the A-share limit-up board for a given trading day."""

    def __init__(self, store: LimitUpStore | None = None) -> None:
        self.store = store if store is not None else LimitUpStore()

    async def run(self, trade_date: date | None = None) -> LimitUpSyncResult:
        """Run the sync and persist records.

        Args:
            trade_date: Date to sync; defaults to today (Shanghai TZ).

        Returns:
            Summary of the sync run.
        """
        if trade_date is None:
            trade_date = _today_shanghai()

        errors: list[str] = []
        # 优先级：akshare (免费/东方财富) → tushare/adshare → 真实 tushare cloud
        sources = ["akshare", "adshare", "tushare_cloud"]
        records: list[LimitUpDaily] = []
        source = "akshare"

        for src in sources:
            try:
                if src == "akshare":
                    records = _akshare_limit_up(trade_date)
                elif src == "adshare":
                    records = _adshare_limit_up(trade_date)
                elif src == "tushare_cloud":
                    records = _fallback_tushare(trade_date)
                if records:
                    source = "tushare/adshare" if src == "adshare" else src
                    break
            except Exception as exc:
                msg = f"{src} failed: {exc}"
                logger.warning(msg)
                errors.append(msg)

        if records:
            try:
                self.store.save(records)
            except Exception as exc:
                errors.append(f"save failed: {exc}")
                raise

        return LimitUpSyncResult(
            trade_date=trade_date,
            count=len(records),
            source=source,
            errors=errors,
        )


