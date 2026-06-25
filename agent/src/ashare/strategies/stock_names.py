"""Lightweight stock-name resolver with fallback cache.

Tries adshare's stock_basic endpoint first, then falls back to the local
``meta/codes.parquet`` shipped with the adshare dataset, and finally to a
small hard-coded map so local development still shows names when everything
else is offline.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


# Fallback names for the default liquid universe used by local_select.
_FALLBACK_NAMES: dict[str, str] = {
    "000001.SZ": "平安银行",
    "000002.SZ": "万科A",
    "000063.SZ": "中兴通讯",
    "000100.SZ": "TCL科技",
    "000333.SZ": "美的集团",
    "000538.SZ": "云南白药",
    "000568.SZ": "泸州老窖",
    "000651.SZ": "格力电器",
    "000725.SZ": "京东方A",
    "000768.SZ": "中航西飞",
    "000858.SZ": "五粮液",
    "000895.SZ": "双汇发展",
    "002001.SZ": "新和成",
    "002007.SZ": "华兰生物",
    "002024.SZ": "苏宁易购",
    "002027.SZ": "分众传媒",
    "002142.SZ": "宁波银行",
    "002230.SZ": "科大讯飞",
    "002236.SZ": "大华股份",
    "002415.SZ": "海康威视",
    "002460.SZ": "赣锋锂业",
    "002475.SZ": "立讯精密",
    "002594.SZ": "比亚迪",
    "002714.SZ": "牧原股份",
    "300014.SZ": "亿纬锂能",
    "300015.SZ": "爱尔眼科",
    "300033.SZ": "同花顺",
    "300059.SZ": "东方财富",
    "300122.SZ": "智飞生物",
    "300124.SZ": "汇川技术",
    "300274.SZ": "阳光电源",
    "300408.SZ": "三环集团",
    "300433.SZ": "蓝思科技",
    "300750.SZ": "宁德时代",
    "600000.SH": "浦发银行",
    "600009.SH": "上海机场",
    "600016.SH": "民生银行",
    "600028.SH": "中国石化",
    "600030.SH": "中信证券",
    "600031.SH": "三一重工",
    "600036.SH": "招商银行",
    "600048.SH": "保利发展",
    "600104.SH": "上汽集团",
    "600196.SH": "复星医药",
    "600276.SH": "恒瑞医药",
    "600309.SH": "万华化学",
    "600406.SH": "国电南瑞",
    "600436.SH": "片仔癀",
    "600519.SH": "贵州茅台",
    "600585.SH": "海螺水泥",
    "600690.SH": "海尔智家",
    "600703.SH": "三安光电",
    "600745.SH": "闻泰科技",
    "600809.SH": "山西汾酒",
    "600837.SH": "海通证券",
    "600887.SH": "伊利股份",
    "600900.SH": "长江电力",
    "601012.SH": "隆基绿能",
    "601066.SH": "中信建投",
    "601088.SH": "中国神华",
    "601166.SH": "兴业银行",
    "601211.SH": "国泰君安",
    "601318.SH": "中国平安",
    "601336.SH": "新华保险",
    "601398.SH": "工商银行",
    "601601.SH": "中国太保",
    "601628.SH": "中国人寿",
    "601668.SH": "中国建筑",
    "601688.SH": "华泰证券",
    "601766.SH": "中国中车",
    "601857.SH": "中国石油",
    "601888.SH": "中国中免",
    "601899.SH": "紫金矿业",
    "601919.SH": "中远海控",
    "601995.SH": "中金公司",
    "603259.SH": "药明康德",
    "603288.SH": "海天味业",
    "603501.SH": "韦尔股份",
    "603986.SH": "兆易创新",
    "605117.SH": "德业股份",
    "688111.SH": "金山办公",
    "688981.SH": "中芯国际",
}


def _detect_data_root() -> Path:
    """Auto-detect adshare data directory."""
    for env_var in ("ADSHARE_DATA_PATH",):
        env_path = __import__("os").environ.get(env_var)
        if env_path:
            return Path(env_path)
    candidates = [
        "/Volumes/mm/project/adshare/data",
        "/Users/lijun/project/adshare/data",
        "/Users/lijun/adshare/data",
        "/app/adshare/data",
    ]
    for c in candidates:
        p = Path(c)
        if p.exists():
            return p
    return Path("/app/adshare/data")


def _load_from_codes_parquet() -> dict[str, str] | None:
    """Load symbol -> name from adshare meta/codes.parquet if available."""
    try:
        import duckdb

        root = _detect_data_root()
        path = root / "meta" / "codes.parquet"
        if not path.exists():
            return None

        con = duckdb.connect(database=":memory:")
        df = con.execute(
            f"SELECT code, name FROM read_parquet('{path}') WHERE code IS NOT NULL"
        ).fetchdf()
        names = {
            str(row["code"]).strip(): str(row["name"]).strip()
            for _, row in df.iterrows()
            if str(row["code"]).strip() and str(row["name"]).strip()
        }
        if len(names) > 100:
            logger.info("loaded %d stock names from %s", len(names), path)
            return names
    except Exception as exc:
        logger.debug("codes.parquet name load failed: %s", exc)
    return None


@lru_cache(maxsize=1)
def _load_name_map() -> dict[str, str]:
    """Load symbol -> name map from adshare, with fallback."""
    try:
        from src.ashare.adshare_client import AdshareClient

        client = AdshareClient()
        resp = client.get_stock_basic()
        if resp and "data" in resp:
            data = resp["data"]
            names = {
                item.get("code", ""): item.get("name", "")
                for item in data
                if item.get("code")
            }
            if len(names) > 100:
                return names
    except Exception as exc:
        logger.debug("adshare stock_basic failed, trying codes.parquet: %s", exc)

    parquet_names = _load_from_codes_parquet()
    if parquet_names:
        return parquet_names

    return _FALLBACK_NAMES.copy()


def get_stock_name(symbol: str) -> str:
    """Return the Chinese name for a symbol, or empty string if unknown."""
    return _load_name_map().get(symbol, "")


def reset_stock_name_cache() -> None:
    """Clear the cached name map (useful in tests)."""
    _load_name_map.cache_clear()
