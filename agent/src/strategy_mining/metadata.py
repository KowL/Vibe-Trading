"""Optional panel metadata enrichment for strategy neutralisation.

Currently only A-share (``csi300``) is supported. The loader tries, in order:

1. **Tushare** (when ``TUSHARE_TOKEN`` is set): adds ``sector`` and
   ``market_cap`` from ``stock_basic``.
2. **akshare** (no token): adds ``sector`` from cninfo's industry-change
   history.  The mapping is cached for 30 days so the slow per-stock lookup
   only runs once.

If enrichment fails or no token is available, the panel is returned unchanged
and neutralisation is skipped.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# 30-day cache for the akshare sector mapping.
_SECTOR_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60


def _sector_cache_path() -> Path:
    return Path.home() / ".vibe-trading" / "cache" / "csi300_sector_map_akshare.json"


def _extract_sector(row: pd.Series) -> str | None:
    """Pick a sector label from a single classification row.

    We keep the original classification vocabulary for each standard instead
    of force-fitting everything into 申银万国 labels.  Mixing standards into
    one label set can put unrelated stocks in the same neutralisation bucket
    (e.g. 中证 "金融" is not the same as SW "非银金融").  The neutraliser only
    needs consistent groups, not a unified ontology.
    """
    std = str(row.get("分类标准", ""))
    category_l1 = str(row.get("行业门类", ""))
    category_l2 = str(row.get("行业大类", ""))

    # 申银万国 (current or old) — use the broad 门类 directly.
    if "申银万国" in std:
        sector = category_l1 or category_l2
        return sector if sector and sector != "nan" else None

    # Other standards (中证, 巨潮, etc.) — prefix the standard name to keep
    # groups internally consistent and avoid collisions with SW labels.
    sector = category_l1 or category_l2
    if sector and sector != "nan":
        return f"{std.split('行业')[0]}:{sector}"

    return None


def _fetch_one_sector_akshare(code: str) -> tuple[str, str | None]:
    """Return (ts_code, sector_label) for one stock via akshare cninfo.

    Tries 申银万国 first, then falls back to other available classification
    standards.  Retries a few times because the cninfo endpoint occasionally
    returns empty payloads under load.
    """
    try:
        import akshare as ak
    except ImportError:
        return code, None

    symbol_code = code.split(".")[0]
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            df = ak.stock_industry_change_cninfo(symbol=symbol_code)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(0.5 * (attempt + 1))
            continue

        if df is None or df.empty:
            return code, None

        # Prefer current SW, then old SW, then 中证, then whatever is available.
        for std in (
            "申银万国行业分类标准",
            "申银万国行业分类标准(旧)",
            "中证行业分类标准",
            "巨潮行业分类标准",
        ):
            subset = df[df["分类标准"] == std]
            if not subset.empty:
                sector = _extract_sector(subset.iloc[-1])
                if sector:
                    return code, sector

        # No known standard matched — return the 门类 of whatever we have.
        sector = _extract_sector(df.iloc[-1])
        return code, sector

    logger.debug(
        "metadata: akshare sector lookup failed for %s after retries: %s",
        code,
        last_exc,
    )
    return code, None


def _load_sector_map_akshare(codes: list[str]) -> dict[str, str]:
    """Load a cached code->sector map or rebuild it from akshare.

    The per-stock cninfo lookup is slow (~a few seconds per call).  We fetch
    sequentially because akshare uses a V8 mini-racer instance that is not
    thread-safe; concurrent access crashes the process.
    """
    cache_path = _sector_cache_path()
    if cache_path.is_file():
        age = time.time() - cache_path.stat().st_mtime
        if age < _SECTOR_CACHE_TTL_SECONDS:
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and cached:
                    logger.info("metadata: loaded akshare sector map from cache (%d codes)", len(cached))
                    return cached
            except Exception as exc:  # noqa: BLE001
                logger.warning("metadata: sector cache read failed: %s", exc)

    sector_map: dict[str, str] = {}
    logger.info("metadata: building akshare sector map for %d codes", len(codes))
    for i, code in enumerate(codes, start=1):
        _, sector = _fetch_one_sector_akshare(code)
        if sector is not None:
            sector_map[code] = sector
        if i % 50 == 0:
            logger.info("metadata: sector map progress %d/%d", i, len(codes))

    if sector_map:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(sector_map, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("metadata: cached akshare sector map (%d/%d codes)", len(sector_map), len(codes))
        except Exception as exc:  # noqa: BLE001
            logger.warning("metadata: sector cache write failed: %s", exc)
    else:
        logger.warning("metadata: akshare sector map empty")

    return sector_map


def _enrich_from_tushare(panel: dict[str, pd.DataFrame], codes: list[str]) -> None:
    """Add sector / market_cap via Tushare pro.stock_basic."""
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token or token == "your-tushare-token":
        return

    try:
        import tushare as ts

        pro = ts.pro_api(token)
        basic = pro.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,industry,total_share",
        )
        if basic is None or basic.empty:
            return

        basic = basic.set_index("ts_code")
        close = panel["close"]
        dates = close.index

        if "sector" not in panel:
            sector_map = {code: str(basic.at[code, "industry"]) for code in codes if code in basic.index}
            if sector_map:
                sector_df = pd.DataFrame(
                    {code: sector_map.get(code, "unknown") for code in codes},
                    index=dates,
                )
                panel["sector"] = sector_df

        if "market_cap" not in panel:
            share_map = {
                code: float(basic.at[code, "total_share"])
                for code in codes
                if code in basic.index and pd.notna(basic.at[code, "total_share"])
            }
            if share_map:
                share_series = pd.Series({code: share_map.get(code, float("nan")) for code in codes})
                panel["market_cap"] = close.mul(share_series, axis=1)

        logger.info(
            "metadata: enriched panel from tushare (sector=%d, market_cap=%d)",
            len(sector_map) if "sector" not in panel else 0,
            len(share_map) if "market_cap" not in panel else 0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("metadata: tushare enrichment failed: %s", exc)


def _enrich_from_akshare(panel: dict[str, pd.DataFrame], codes: list[str]) -> None:
    """Add sector via akshare cninfo (no token required)."""
    if "sector" in panel:
        return

    sector_map = _load_sector_map_akshare(codes)
    if not sector_map:
        return

    close = panel["close"]
    dates = close.index
    sector_df = pd.DataFrame(
        {code: sector_map.get(code, "unknown") for code in codes},
        index=dates,
    )
    panel["sector"] = sector_df
    logger.info("metadata: enriched panel from akshare (sector=%d)", len(sector_map))


def enrich_panel(panel: dict[str, pd.DataFrame], universe: str) -> dict[str, pd.DataFrame]:
    """Try to add sector / market_cap columns to ``panel`` in-place."""
    if universe != "csi300":
        return panel

    if "sector" in panel and "market_cap" in panel:
        return panel

    close = panel.get("close")
    if close is None or close.empty:
        return panel

    codes = list(close.columns)

    # Prefer Tushare when available (provides both sector and total shares).
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if token and token != "your-tushare-token":
        _enrich_from_tushare(panel, codes)

    # Fall back to akshare for sector mapping when no token or tushare missed it.
    if "sector" not in panel:
        _enrich_from_akshare(panel, codes)

    return panel
