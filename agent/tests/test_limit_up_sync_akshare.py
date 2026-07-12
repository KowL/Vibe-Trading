"""Tests for the AkShare data source in limit_up_sync.

Mocks akshare so the test runs offline and exercises the field-mapping logic.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any

import pandas as pd
import pytest

from src.ashare.tasks import limit_up_sync


class _FakeAkshare:
    """Drop-in for the `ak` module exposing the two pool functions used by sync."""

    def __init__(self, sealed: pd.DataFrame | None, broken: pd.DataFrame | None, raise_broken: bool = False) -> None:
        self._sealed = sealed
        self._broken = broken
        self._raise_broken = raise_broken

    def stock_zt_pool_em(self, date: str = "") -> pd.DataFrame:  # noqa: ARG002 — signature matches ak
        assert self._sealed is not None, "sealed pool not configured for this test"
        return self._sealed

    def stock_zt_pool_zbgc_em(self, date: str = "") -> pd.DataFrame:  # noqa: ARG002
        if self._raise_broken:
            raise RuntimeError("炸板股池只能获取最近 30 个交易日的数据")
        assert self._broken is not None, "broken pool not configured for this test"
        return self._broken


def _sealed_row(**overrides: Any) -> dict[str, Any]:
    base = {
        "代码": "000001",
        "名称": "平安银行",
        "涨跌幅": 9.98,
        "最新价": 13.20,
        "成交额": 1_500_000_000,    # 元
        "换手率": 2.34,
        "封板资金": 80_000_000,        # 元
        "首次封板时间": "093501",
        "最后封板时间": "150000",
        "炸板次数": 0,
        "涨停统计": "1天1板",
        "连板数": 1,
        "所属行业": "银行",
    }
    base.update(overrides)
    return base


def _broken_row(**overrides: Any) -> dict[str, Any]:
    base = {
        "代码": "600519",
        "名称": "贵州茅台",
        "涨跌幅": 4.50,
        "最新价": 1700.50,
        "涨停价": 1883.34,
        "成交额": 5_000_000_000,    # 元
        "换手率": 0.85,
        "首次封板时间": "100245",
        "炸板次数": 2,
        "涨停统计": "5天5板",
        "所属行业": "白酒",
    }
    base.update(overrides)
    return base


@pytest.fixture
def fake_ak(monkeypatch: pytest.MonkeyPatch) -> _FakeAkshare:
    fake = _FakeAkshare(sealed=pd.DataFrame(), broken=pd.DataFrame())
    monkeypatch.setattr(limit_up_sync, "_akshare_limit_up", None)  # placeholder, will be set per test
    return fake


def test_akshare_sealed_pool_unit_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeAkshare(
        sealed=pd.DataFrame([_sealed_row()]),
        broken=pd.DataFrame(),
    )
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)

    records = limit_up_sync._akshare_limit_up(date(2025, 6, 10))
    assert len(records) == 1
    r = records[0]
    assert r.symbol == "000001.SZ"
    assert r.name == "平安银行"
    assert r.limit_up_count == 1
    assert r.limit_up_price == 13.20            # sealed 池无涨停价字段，用 close 顶替
    assert r.close_price == 13.20
    assert r.change_pct == pytest.approx(0.0998)
    assert r.turnover_amount == 1_500_000_000   # 元（未乘 10000）
    assert r.turnover_ratio == pytest.approx(0.0234)
    assert r.seal_amount == 80_000_000
    assert r.first_time == time(9, 35, 1)
    assert r.last_time == time(15, 0, 0)
    assert r.open_count is None
    assert r.industry == "银行"
    assert r.is_sealed is True
    assert r.source == "akshare"


def test_akshare_broken_pool_extracts_consecutive_from_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeAkshare(
        sealed=pd.DataFrame(),
        broken=pd.DataFrame([_broken_row()]),
    )
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)

    records = limit_up_sync._akshare_limit_up(date(2025, 6, 10))
    assert len(records) == 1
    r = records[0]
    assert r.symbol == "600519.SH"
    assert r.limit_up_count == 5                # 从 "5天5板" 解析
    assert r.limit_up_price == 1883.34          # 炸板池直接提供
    assert r.close_price == 1700.50
    assert r.is_sealed is False                 # close < limit → 自动判定
    assert r.seal_amount is None                # 炸板池无封板资金
    assert r.open_count == 2                    # 炸板次数
    assert r.first_time == time(10, 2, 45)


def test_akshare_combines_sealed_and_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeAkshare(
        sealed=pd.DataFrame([_sealed_row(symbol="000001"), _sealed_row(code_override="000002")]),
        broken=pd.DataFrame([_broken_row()]),
    )
    fake = _FakeAkshare(
        sealed=pd.DataFrame([
            _sealed_row(),
            {**_sealed_row(), "代码": "000002", "名称": "万科A", "连板数": 2},
        ]),
        broken=pd.DataFrame([_broken_row()]),
    )
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)

    records = limit_up_sync._akshare_limit_up(date(2025, 6, 10))
    assert {r.symbol for r in records} == {"000001.SZ", "000002.SZ", "600519.SH"}
    assert sum(1 for r in records if r.is_sealed) == 2
    assert sum(1 for r in records if not r.is_sealed) == 1


def test_akshare_handles_broken_pool_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """炸板池 30 天之外的日期会抛错；同步应当忽略失败并继续。"""
    fake = _FakeAkshare(
        sealed=pd.DataFrame([_sealed_row()]),
        broken=None,
        raise_broken=True,
    )
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)

    records = limit_up_sync._akshare_limit_up(date(2025, 1, 2))
    assert len(records) == 1
    assert records[0].is_sealed is True


def test_akshare_missing_module_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """akshare 未安装时，不抛异常，返回空列表。"""
    import importlib
    import sys

    sys.modules.pop("akshare", None)

    # _akshare_limit_up 内 lazy import；模拟 ImportError
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "akshare" or name.startswith("akshare."):
            raise ImportError("akshare not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded_import)

    records = limit_up_sync._akshare_limit_up(date(2025, 6, 10))
    assert records == []


def test_parse_time_handles_hhmmss_without_colons() -> None:
    assert limit_up_sync._parse_time("092500") == time(9, 25, 0)
    assert limit_up_sync._parse_time("150001") == time(15, 0, 1)
    # 兼容 ISO 风格
    assert limit_up_sync._parse_time("09:25:00") == time(9, 25, 0)
    assert limit_up_sync._parse_time("09:25") == time(9, 25, 0)
    # 空值 / None
    assert limit_up_sync._parse_time(None) is None
    assert limit_up_sync._parse_time("") is None
    # 非法值
    assert limit_up_sync._parse_time("xx") is None


def test_run_prioritizes_akshare(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """LimitUpSyncTask.run() 应当先尝试 akshare，且采用其结果（如果成功）。"""
    sealed = pd.DataFrame([_sealed_row()])

    def fake_akshare(_date: date) -> list:
        rec = limit_up_sync.LimitUpDaily(
            trade_date=_date,
            symbol="000001.SZ",
            name="平安银行",
            limit_up_count=1,
            limit_up_price=13.20,
            close_price=13.20,
            source="akshare",
        )
        return [rec]

    def should_not_be_called(_date: date) -> list:  # pragma: no cover — guard
        raise AssertionError("tushare/adshare should not be called when akshare succeeds")

    monkeypatch.setattr(limit_up_sync, "_akshare_limit_up", fake_akshare)
    monkeypatch.setattr(limit_up_sync, "_adshare_limit_up", should_not_be_called)
    monkeypatch.setattr(limit_up_sync, "_fallback_tushare", should_not_be_called)

    # 用临时 store 跑，但注意 store 内部硬编码到 ~/.vibe-trading；这里只验证不报错
    task = limit_up_sync.LimitUpSyncTask()
    import asyncio
    result = asyncio.run(task.run(date(2025, 6, 10)))
    assert result.source == "akshare"
    assert result.count == 1
    assert result.errors == []
