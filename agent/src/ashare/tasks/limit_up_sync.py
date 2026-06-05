"""A-share limit-up data sync task.

Ports Ruo.ai's 15:30 涨停同步任务 to Vibe-Trading's asyncio scheduler.
Fetches the day's limit-up board from AmazingData (fallback tushare/akshare),
parses into LimitUpDaily records, and persists to ~/.vibe-trading/ashare/limit_up/.
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

logger = logging.getLogger(__name__)

_AMAZINGDATA_BASE = "http://127.0.0.1:3100"


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
    """Normalize AmazingData symbol to Vibe-Trading style (e.g. 000001.SZ)."""
    raw = str(raw).strip().upper()
    if "." in raw:
        return raw
    # Heuristic: 6-digit codes starting with 6 are SH, otherwise SZ
    if raw.startswith("6"):
        return f"{raw}.SH"
    return f"{raw}.SZ"


def _parse_time(value: Any) -> time | None:
    """Parse '09:30:00' or '09:30' into a time object."""
    if value is None or value == "":
        return None
    s = str(value).strip()
    if len(s) >= 6:
        try:
            return time.fromisoformat(s)
        except ValueError:
            pass
    return None


def _amazingdata_limit_up(trade_date: date) -> list[LimitUpDaily]:
    """Fetch limit-up board from AmazingData /stock/limit-up."""
    url = f"{_AMAZINGDATA_BASE}/stock/limit-up"
    params = {"date": trade_date.isoformat()}
    r = httpx.get(url, params=params, timeout=30.0)
    r.raise_for_status()
    payload = r.json()
    rows = payload.get("data", []) if isinstance(payload, dict) else payload
    records: list[LimitUpDaily] = []
    for row in rows:
        symbol = _normalize_symbol(row.get("code") or row.get("symbol", ""))
        if not symbol or symbol == ".SH" or symbol == ".SZ":
            continue
        rec = LimitUpDaily(
            trade_date=trade_date,
            symbol=symbol,
            name=row.get("name", ""),
            limit_up_count=int(row.get("limit_up_count") or row.get("limit_up_times") or 1),
            limit_up_price=_float(row.get("limit_up_price")),
            open_price=_float(row.get("open")),
            close_price=_float(row.get("close")),
            high_price=_float(row.get("high")),
            low_price=_float(row.get("low")),
            prev_close=_float(row.get("pre_close")),
            change_pct=_float(row.get("change_pct")),
            turnover_amount=_float(row.get("turnover_amount")),
            turnover_volume=_float(row.get("turnover_volume")),
            turnover_ratio=_float(row.get("turnover_ratio")),
            seal_amount=_float(row.get("seal_amount")),
            seal_ratio=_float(row.get("seal_ratio")),
            first_time=_parse_time(row.get("first_time")),
            last_time=_parse_time(row.get("last_time")),
            open_count=int(row.get("open_count") or 0),
            industry=row.get("industry", ""),
            concept=row.get("concept", ""),
            reason=row.get("reason", ""),
            source="amazingdata",
        )
        records.append(rec)
    return records


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


def _fallback_akshare(trade_date: date) -> list[LimitUpDaily]:
    """Placeholder: akshare fallback."""
    try:
        import akshare as ak
    except ImportError:
        return []
    try:
        df = ak.stock_zt_pool_em(date=trade_date.strftime("%Y%m%d"))
    except Exception:
        return []
    records: list[LimitUpDaily] = []
    for _, row in df.iterrows():
        symbol = _normalize_symbol(row.get("代码", ""))
        if not symbol:
            continue
        records.append(
            LimitUpDaily(
                trade_date=trade_date,
                symbol=symbol,
                name=row.get("名称", ""),
                limit_up_count=int(row.get("连板数", 1)),
                close_price=_float(row.get("最新价")),
                limit_up_price=_float(row.get("涨停价")),
                turnover_amount=_float(row.get("成交额")) * 10000,
                turnover_ratio=_float(row.get("换手率")),
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
        sources = ["amazingdata", "tushare", "akshare"]
        records: list[LimitUpDaily] = []
        source = "amazingdata"

        for src in sources:
            try:
                if src == "amazingdata":
                    records = _amazingdata_limit_up(trade_date)
                elif src == "tushare":
                    records = _fallback_tushare(trade_date)
                elif src == "akshare":
                    records = _fallback_akshare(trade_date)
                if records:
                    source = src
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


