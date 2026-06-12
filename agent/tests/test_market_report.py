"""Tests for A-share market report generator."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.ashare.models.limit_up import LimitUpDaily
from src.ashare.storage.limit_up_store import LimitUpStore
from src.ashare.tasks.market_report import (
    MarketReportTask,
    ReportKind,
    _collect_metrics,
)


def _make_record(**kwargs) -> LimitUpDaily:
    defaults = {
        "trade_date": date(2025, 6, 10),
        "symbol": "000001.SZ",
        "name": "平安银行",
        "limit_up_count": 1,
    }
    defaults.update(kwargs)
    return LimitUpDaily(**defaults)


def test_broken_count_uses_not_sealed_not_opened(tmp_path) -> None:
    """炸板家数 must count records that failed to seal at close, not any record
    whose board opened intra-day.
    """
    store = LimitUpStore(root=tmp_path / "limit_up")
    store.save(
        [
            # sealed without ever opening
            _make_record(symbol="S1", close_price=10.0, limit_up_price=10.0, open_count=None),
            # sealed but opened 3 times during the day
            _make_record(symbol="S2", close_price=11.0, limit_up_price=11.0, open_count=3),
            # not sealed at close (true 炸板)
            _make_record(symbol="B1", close_price=9.0, limit_up_price=10.0, open_count=2),
        ]
    )

    metrics = _collect_metrics(date(2025, 6, 10), store)
    assert metrics.limit_up_count == 2
    assert metrics.limit_up_opened_count == 1


def test_hot_industries_filled_when_concept_missing(tmp_path) -> None:
    """AkShare does not populate 'concept', but it does populate 'industry'.
    Reports should still show a hot-industry table.
    """
    store = LimitUpStore(root=tmp_path / "limit_up")
    store.save(
        [
            _make_record(symbol="A", industry="半导体", seal_amount=100_000_000),
            _make_record(symbol="B", industry="半导体", seal_amount=50_000_000),
            _make_record(symbol="C", industry="化学制品", seal_amount=80_000_000),
        ]
    )

    metrics = _collect_metrics(date(2025, 6, 10), store)
    assert metrics.hot_concepts == []
    assert len(metrics.hot_industries) == 2
    assert metrics.hot_industries[0]["name"] == "半导体"
    assert metrics.hot_industries[0]["count"] == 2


def test_close_report_contains_hot_industry_table(tmp_path, monkeypatch) -> None:
    """Generated close report should render the hot-industry table and use
    the AkShare source label.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    store = LimitUpStore(root=tmp_path / "limit_up")
    store.save(
        [
            _make_record(symbol="A", industry="半导体", seal_amount=100_000_000, source="akshare"),
            _make_record(symbol="B", industry="半导体", seal_amount=50_000_000, source="akshare"),
        ]
    )

    task = MarketReportTask(store)
    report = task.run_sync(ReportKind.CLOSE, date(2025, 6, 10))

    assert "## 热门行业 TOP10" in report.markdown
    assert "| 半导体 | 2 |" in report.markdown
    assert "akshare" in report.markdown.lower()
    assert report.metrics["hot_industries"]
    assert report.metrics["hot_concepts"] == []


# Helpers attached to the task class for easier synchronous testing.
def _run_sync(self, kind: ReportKind, trade_date: date | None = None):
    import asyncio

    return asyncio.run(self.run(kind, trade_date))


MarketReportTask.run_sync = _run_sync  # type: ignore[attr-defined]
