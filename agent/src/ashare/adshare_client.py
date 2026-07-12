"""Adshare HTTP API client for Vibe-Trading A-share extension.

Provides a thin wrapper over adshare's REST API (localhost:8000).
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import httpx

_ADSHARE_BASE = os.environ.get("ADSHARE_URL", "http://localhost:8000")


def _fmt_date(d: date | str | int | None) -> int | None:
    """Normalize a date input to YYYYMMDD integer as expected by adshare.

    Accepts ``date`` objects, ISO strings (``YYYY-MM-DD``), or already
    packed integers (``YYYYMMDD``). Returns ``None`` for ``None``.
    """
    if d is None:
        return None
    if isinstance(d, int):
        return d
    if isinstance(d, date):
        return int(d.strftime("%Y%m%d"))
    s = str(d).replace("-", "")
    return int(s)


class AdshareClient:
    """HTTP client for adshare data service."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or _ADSHARE_BASE).rstrip("/")
        self._client = httpx.Client(timeout=30.0)

    # --------------------------------------------------------------------- #
    # Market data                                                           #
    # --------------------------------------------------------------------- #

    def get_limit_up(self, trade_date: date | None = None, days: int = 1, board_filter: str = "all", exclude_st: bool = True) -> dict[str, Any]:
        """Fetch limit-up board from adshare /market/limit-up."""
        params: dict[str, Any] = {
            "days": days,
            "board_filter": board_filter,
            "exclude_st": str(exclude_st).lower(),
        }
        packed = _fmt_date(trade_date)
        if packed is not None:
            params["date"] = packed
        r = self._client.get(f"{self.base_url}/market/limit-up", params=params)
        r.raise_for_status()
        return r.json()

    def get_limit_up_ladder(self, days: int = 15) -> dict[str, Any]:
        """Fetch limit-up ladder from adshare /market/limit-up/ladder."""
        params = {"days": days}
        r = self._client.get(f"{self.base_url}/market/limit-up/ladder", params=params)
        r.raise_for_status()
        return r.json()

    def get_snapshot(self, codes: list[str]) -> dict[str, Any]:
        """Fetch stock snapshot from adshare /market/snapshot."""
        codes_str = ",".join(codes)
        r = self._client.get(f"{self.base_url}/market/snapshot", params={"codes": codes_str})
        r.raise_for_status()
        return r.json()

    def get_kline(
        self,
        code: str,
        period: str = "daily",
        begin_date: int | str | None = None,
        end_date: int | str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Fetch K-line data and return the adshare-compatible ``{data: [...]}`` shape.

        ``limit`` is ignored for tushare; kept for backward compatibility.
        """
        params: dict[str, Any] = {
            "codes": code,
            "period": period,
        }
        packed_begin = _fmt_date(begin_date)
        packed_end = _fmt_date(end_date)
        if packed_begin is not None:
            params["begin_date"] = packed_begin
        if packed_end is not None:
            params["end_date"] = packed_end
        if limit is not None:
            params["limit"] = limit
        r = self._client.get(f"{self.base_url}/market/kline", params=params)
        r.raise_for_status()
        return r.json()

    def get_stock_basic(self, codes: list[str] | None = None) -> dict[str, Any]:
        """Fetch stock basic info from adshare /market/stock/basic."""
        params: dict[str, Any] = {}
        if codes:
            params["codes"] = ",".join(codes)
        r = self._client.get(f"{self.base_url}/market/stock/basic", params=params)
        r.raise_for_status()
        return r.json()

    def health(self) -> dict[str, Any]:
        """Check adshare health."""
        r = self._client.get(f"{self.base_url}/health")
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> AdshareClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
