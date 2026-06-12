"""Fast local data loader for backtesting using DuckDB/Parquet.

Bypasses HTTP API and reads directly from adshare's on-disk Parquet files.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)


class LocalKlineLoader:
    """Load K-line data directly from local Parquet files.

    Usage:
        loader = LocalKlineLoader()
        df = loader.load("000001.SZ", "20240101", "20241231")
    """

    def __init__(self, data_root: str | None = None) -> None:
        """Initialize with data root path.

        Args:
            data_root: Path to adshare data directory. If None, auto-detects
                      from ADSHARE_DATA_PATH env or common locations.
        """
        self.data_root = Path(self._detect_root(data_root))
        self._con = duckdb.connect(database=":memory:")
        self._con.execute(f"PRAGMA threads={max(1, (os.cpu_count() or 2))}")
        logger.info("LocalKlineLoader: data_root=%s", self.data_root)

    def _detect_root(self, explicit: str | None) -> str:
        """Auto-detect adshare data directory."""
        if explicit:
            return explicit
        # Try env var
        env_path = os.environ.get("ADSHARE_DATA_PATH")
        if env_path:
            return env_path
        # Try common locations
        candidates = [
            "/Volumes/mm/project/adshare/data",
            "/Users/lijun/project/adshare/data",
            "/Users/lijun/adshare/data",
            "/app/adshare/data",
        ]
        for c in candidates:
            if Path(c).exists():
                return c
        raise RuntimeError(
            "Cannot find adshare data directory. "
            "Set ADSHARE_DATA_PATH env or pass data_root explicitly."
        )

    def load(
        self,
        symbol: str,
        begin_date: str | int,
        end_date: str | int,
        period: str = "daily",
    ) -> pd.DataFrame | None:
        """Load K-line for a single symbol.

        Args:
            symbol: Stock code with suffix (e.g. "000001.SZ")
            begin_date: YYYYMMDD
            end_date: YYYYMMDD
            period: "daily" | "weekly" | "monthly"

        Returns:
            DataFrame with columns: date, open, high, low, close, volume, amount
            or None if file not found
        """
        subdir = self._normalize_period(period)
        safe_code = self._safe_code(symbol)
        file_path = self.data_root / "A_share" / subdir / f"{safe_code}.parquet"

        if not file_path.exists():
            logger.debug("Parquet not found: %s", file_path)
            return None

        begin = int(begin_date)
        end = int(end_date)

        sql = f"""
            SELECT
                date, open, high, low, close, volume, amount
            FROM read_parquet('{file_path}')
            WHERE date BETWEEN {begin} AND {end}
            ORDER BY date
        """
        try:
            df = self._con.execute(sql).fetchdf()
            if df.empty:
                return None
            df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
            df = df.set_index("date").sort_index()
            return df
        except Exception as exc:
            logger.warning("load failed for %s: %s", symbol, exc)
            return None

    def load_batch(
        self,
        symbols: list[str],
        begin_date: str | int,
        end_date: str | int,
        period: str = "daily",
    ) -> dict[str, pd.DataFrame]:
        """Load K-line for multiple symbols efficiently.

        Uses a single DuckDB query with read_parquet union.
        """
        subdir = self._normalize_period(period)
        begin = int(begin_date)
        end = int(end_date)

        # Build file list
        file_paths: list[str] = []
        valid_symbols: list[str] = []
        for sym in symbols:
            safe = self._safe_code(sym)
            path = self.data_root / "A_share" / subdir / f"{safe}.parquet"
            if path.exists():
                file_paths.append(str(path))
                valid_symbols.append(sym)

        if not file_paths:
            return {}

        # Single query for all symbols
        file_list = "[" + ",".join(f"'{p}'" for p in file_paths) + "]"
        sql = f"""
            SELECT
                regexp_extract(filename, '.*/([^/]+)\\.parquet$', 1) AS code,
                date, open, high, low, close, volume, amount
            FROM read_parquet({file_list}, filename=true, union_by_name=true)
            WHERE date BETWEEN {begin} AND {end}
            ORDER BY code, date
        """
        try:
            df = self._con.execute(sql).fetchdf()
            if df.empty:
                return {}

            df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
            df = df.set_index("date").sort_index()

            # Split by symbol
            result: dict[str, pd.DataFrame] = {}
            for sym in valid_symbols:
                safe = self._safe_code(sym)
                sym_df = df[df["code"] == safe].copy()
                if not sym_df.empty:
                    sym_df = sym_df.drop(columns=["code"])
                    result[sym] = sym_df
            return result
        except Exception as exc:
            logger.warning("load_batch failed: %s", exc)
            return {}

    def _normalize_period(self, period: str) -> str:
        """Normalize period to directory name."""
        p = period.lower()
        if p in ("day", "daily", "d"):
            return "daily"
        elif p in ("week", "weekly", "w"):
            return "weekly"
        elif p in ("month", "monthly", "m"):
            return "monthly"
        return p

    def _safe_code(self, code: str) -> str:
        """Ensure code has suffix."""
        c = code.strip()
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


class CachedMultiFactorSelector:
    """MultiFactorSelector with pre-loaded data cache.

    Loads all K-line data once at initialization, then runs selection
    entirely in-memory without HTTP requests.
    """

    def __init__(
        self,
        data_root: str | None = None,
        universe: list[str] | None = None,
    ) -> None:
        self.loader = LocalKlineLoader(data_root)
        self.universe = universe or self._default_universe()
        self._cache: dict[str, pd.DataFrame] = {}

    def preload(
        self,
        begin_date: str | int,
        end_date: str | int,
        period: str = "daily",
    ) -> None:
        """Pre-load all K-line data into memory."""
        logger.info(
            "preload: %d symbols, %s ~ %s",
            len(self.universe),
            begin_date,
            end_date,
        )
        self._cache = self.loader.load_batch(
            self.universe, begin_date, end_date, period
        )
        logger.info("preload: loaded %d symbols", len(self._cache))

    def get_data(self, symbol: str) -> pd.DataFrame | None:
        """Get cached data for a symbol."""
        return self._cache.get(symbol)

    def _default_universe(self) -> list[str]:
        """Default liquid A-share universe."""
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
