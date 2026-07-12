"""A-share tushare-based data client with akshare fallback.

Replaces the previous adshare client.  Reads from a local tushare-compatible
endpoint (adshare `/dataapi`) by default, or from the real tushare cloud API if
configured.  Falls back to akshare for any missing endpoints or connection
errors.

Environment variables
---------------------
TUSHARE_TOKEN
    Real tushare token.  Optional; the local `/dataapi` endpoint ignores it.
TUSHARE_BASE_URL
    Base URL for the tushare endpoint.  Defaults to the local adshare
    container: http://127.0.0.1:8000/dataapi
ASHARE_DATA_PATH
    Local adshare data directory, still used by LocalKlineLoader / stock_names
    if available.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd
import tushare as ts

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Configuration helpers
# --------------------------------------------------------------------------- #


def _get_tushare_token() -> str:
    """Return a non-empty token for tushare SDK initialization.

    The local `/dataapi` endpoint does not validate the token, but tushare's
    ``pro_api()`` requires it to be non-empty.
    """
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token or token.lower() in {"your-tushare-token", "your_token_here"}:
        # Dummy token for local endpoint
        return "000000000000000000000000000000000000000000000000"
    return token


def _get_tushare_base_url() -> str:
    """Return the tushare endpoint base URL."""
    return os.getenv("TUSHARE_BASE_URL", "http://127.0.0.1:8000/dataapi").rstrip("/")


def _is_local_endpoint() -> bool:
    """True if the configured endpoint looks like the local adshare container."""
    base = _get_tushare_base_url()
    return "127.0.0.1" in base or "localhost" in base


# --------------------------------------------------------------------------- #
# Akshare fallback helpers
# --------------------------------------------------------------------------- #


def _akshare_stock_daily(symbol: str, start_date: str | None, end_date: str | None) -> pd.DataFrame | None:
    """Fetch daily K-line via akshare.

    symbol is like 000001.SZ; akshare needs 000001 without suffix.
    """
    try:
        import akshare as ak
    except Exception as exc:  # noqa: BLE001
        logger.debug("akshare not available: %s", exc)
        return None

    code = symbol.split(".")[0] if "." in symbol else symbol
    try:
        # akshare.stock_zh_a_hist returns Chinese-column DataFrame
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date or "",
            end_date=end_date or "",
            adjust="",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("akshare stock_zh_a_hist failed for %s: %s", symbol, exc)
        return None

    if df is None or df.empty:
        return None

    df = df.rename(
        columns={
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "振幅": "amplitude",
            "涨跌幅": "pct_chg",
            "涨跌额": "change",
            "换手率": "turnover",
        }
    )
    for col in ("open", "high", "low", "close", "volume", "amount", "amplitude", "pct_chg", "change", "turnover"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")
    # Keep only the standard columns expected by downstream callers
    keep = [c for c in ("open", "high", "low", "close", "volume", "amount") if c in df.columns]
    df = df[keep].copy()
    df = df.dropna(subset=["open", "high", "low", "close"])
    if df.empty:
        return None
    return df


# --------------------------------------------------------------------------- #
# Main client
# --------------------------------------------------------------------------- #


class TushareClient:
    """Tushare-first data client with akshare fallback for A-share data."""

    def __init__(self, token: str | None = None, base_url: str | None = None) -> None:
        self.token = (token or _get_tushare_token()).strip()
        self.base_url = (base_url or _get_tushare_base_url()).rstrip("/")
        self._pro: ts.pro_api | None = None

    # ----------------------------------------------------------------------- #
    # Internal tushare pro handle
    # ----------------------------------------------------------------------- #

    def _get_pro(self) -> ts.pro_api:
        """Return a tushare pro_api instance pointed at the configured base URL."""
        if self._pro is None:
            self._pro = ts.pro_api(self.token)
            self._pro._DataApi__http_url = self.base_url
        return self._pro

    # ----------------------------------------------------------------------- #
    # K-line data (daily / weekly / monthly)
    # ----------------------------------------------------------------------- #

    def get_kline(
        self,
        code: str,
        period: str = "daily",
        begin_date: int | str | None = None,
        end_date: int | str | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame | None:
        """Fetch K-line data for a single symbol.

        Returns a DataFrame with date index and columns:
        open, high, low, close, volume, amount (amount may be missing).
        ``limit`` is ignored for tushare; kept for backward compatibility.
        """
        code = _normalize_code(code)
        api_name = _period_to_api(period)
        start = _date_to_ymd(begin_date)
        end = _date_to_ymd(end_date)

        pro = self._get_pro()
        try:
            df = pro.query(
                api_name,
                ts_code=code,
                start_date=start,
                end_date=end,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("tushare %s failed for %s: %s", api_name, code, exc)
            df = pd.DataFrame()

        if df is not None and not df.empty and _is_local_endpoint():
            # Local adshare endpoint currently returns all history for the symbol
            # when start/end are not fully populated; filter to the requested range.
            df = _filter_tushare_df(df, start, end)

        if df is not None and not df.empty:
            return _tushare_kline_to_standard(df)

        # Fallback to akshare
        logger.info("kline fallback to akshare for %s %s", code, period)
        return _akshare_stock_daily(code, start, end)

    # ----------------------------------------------------------------------- #
    # Stock basic info
    # ----------------------------------------------------------------------- #

    def get_stock_basic(self, code: str | None = None) -> dict[str, Any]:
        """Return stock basic information as a dict compatible with adshare response.

        Response format: {"data": [{"code": ..., "name": ...}, ...]}
        """
        pro = self._get_pro()
        try:
            params: dict[str, Any] = {"exchange": "", "list_status": "L"}
            if code:
                params["ts_code"] = _normalize_code(code)
            df = pro.stock_basic(**params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("tushare stock_basic failed: %s", exc)
            df = pd.DataFrame()

        if df is not None and not df.empty:
            return {"data": _tushare_basic_to_records(df)}

        return {"data": []}

    # ----------------------------------------------------------------------- #
    # Market/limit-up endpoints via the adshare API directly
    # ----------------------------------------------------------------------- #

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Make a simple HTTP request to the adshare API root.

        path is relative to the base URL *without* the /dataapi prefix, e.g.
        ``/market/limit-up``.  We derive the API root by stripping /dataapi.
        """
        import requests

        root = self.base_url.removesuffix("/dataapi")
        url = f"{root}{path}"
        try:
            resp = requests.request(method, url, timeout=30, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("adshare API request %s %s failed: %s", method, url, exc)
            return {}

    def get_limit_up(self, days: int = 1, date: int | str | None = None) -> dict[str, Any]:
        """Return limit-up stocks from the adshare ``/market/limit-up`` endpoint."""
        params: dict[str, Any] = {"days": days}
        if date is not None:
            params["date"] = int(_date_to_ymd(date))
        return self._request("GET", "/market/limit-up", params=params)

    def get_limit_up_ladder(self, days: int = 15, date: int | str | None = None) -> dict[str, Any]:
        """Return limit-up ladder from the adshare ``/market/limit-up/ladder`` endpoint."""
        params: dict[str, Any] = {"days": days}
        if date is not None:
            params["date"] = int(_date_to_ymd(date))
        return self._request("GET", "/market/limit-up/ladder", params=params)

    def get_market_activity(self, date: int | str | None = None) -> dict[str, Any]:
        """Return market activity from the adshare ``/market/market-activity`` endpoint."""
        params: dict[str, Any] = {}
        if date is not None:
            params["date"] = int(_date_to_ymd(date))
        return self._request("GET", "/market/market-activity", params=params)

    # ----------------------------------------------------------------------- #
    # Backward-compatible adshare methods used in the codebase
    # ----------------------------------------------------------------------- #

    def get_snapshot(self, symbols: list[str]) -> dict[str, Any]:
        """Return a snapshot quote from the adshare ``/market/snapshot`` endpoint.

        This is a convenience wrapper kept for backward compatibility with
        callers that used ``AdshareClient.get_snapshot``.
        """
        codes = ",".join(_normalize_code(s) for s in symbols)
        return self._request("GET", "/market/snapshot", params={"codes": codes})

    def close(self) -> None:
        """No-op.  Kept for compatibility with old AdshareClient."""
        pass

    def __enter__(self) -> "TushareClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _normalize_code(code: str) -> str:
    """Ensure code has exchange suffix (tushare format)."""
    c = code.strip().upper()
    if "." in c:
        return c
    if len(c) == 6 and c.isdigit():
        if c.startswith(("60", "68", "69")):
            return f"{c}.SH"
        elif c.startswith(("00", "30", "39")):
            return f"{c}.SZ"
        elif c.startswith(("8", "4", "9")):
            return f"{c}.BJ"
    return c


def _period_to_api(period: str) -> str:
    """Map a period name to the tushare API name."""
    p = period.lower()
    if p in ("day", "daily", "d"):
        return "daily"
    if p in ("week", "weekly", "w"):
        return "weekly"
    if p in ("month", "monthly", "m"):
        return "monthly"
    return p


def _date_to_ymd(value: int | str | None) -> str:
    """Convert int/str date to YYYYMMDD.  Empty strings are returned as-is."""
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    v = str(value).strip().replace("-", "")
    return v


def _filter_tushare_df(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """Filter a tushare DataFrame to a [start, end] date range."""
    if "trade_date" not in df.columns:
        return df
    df = df.copy()
    df["trade_date_int"] = pd.to_numeric(df["trade_date"], errors="coerce").fillna(0).astype(int)
    if start:
        df = df[df["trade_date_int"] >= int(start)]
    if end:
        df = df[df["trade_date_int"] <= int(end)]
    df = df.drop(columns=["trade_date_int"])
    return df


def _tushare_kline_to_standard(df: pd.DataFrame) -> pd.DataFrame | None:
    """Normalize a tushare K-line DataFrame to the internal column convention."""
    if df is None or df.empty:
        return None
    df = df.copy()

    # Date index
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
        df = df.set_index("trade_date").sort_index()
    elif "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        df = df.set_index("date").sort_index()

    # Rename columns to internal convention
    column_map = {
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "vol": "volume",
        "volume": "volume",
        "amount": "amount",
    }
    df = df.rename(columns=column_map)

    numeric_cols = ["open", "high", "low", "close", "volume", "amount"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    keep = [c for c in ("open", "high", "low", "close", "volume", "amount") if c in df.columns]
    df = df[keep].dropna(subset=["open", "high", "low", "close"])
    if df.empty:
        return None
    return df


def _tushare_basic_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert tushare stock_basic DataFrame to adshare-like records."""
    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        record: dict[str, Any] = {}
        if "ts_code" in df.columns:
            record["code"] = str(row["ts_code"]).strip()
        if "name" in df.columns:
            record["name"] = str(row["name"]).strip()
        if "list_date" in df.columns:
            record["list_date"] = row["list_date"]
        if "delist_date" in df.columns:
            record["delist_date"] = row["delist_date"]
        if "is_listed" in df.columns:
            record["is_listed"] = row["is_listed"]
        if "board" in df.columns:
            record["board"] = str(row["board"]).strip()
        if "industry" in df.columns:
            record["industry"] = str(row["industry"]).strip()
        records.append(record)
    return records


# Singleton for convenience, matching the previous AdshareClient pattern.
_client: TushareClient | None = None


def get_tushare_client() -> TushareClient:
    """Return the global default TushareClient instance."""
    global _client
    if _client is None:
        _client = TushareClient()
    return _client


def reset_tushare_client() -> None:
    """Reset the global client (useful in tests)."""
    global _client
    _client = None
