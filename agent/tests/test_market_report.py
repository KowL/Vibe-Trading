"""Tests for A-share market report generator."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from src.ashare.models.limit_up import LimitUpDaily
from src.ashare.storage.limit_up_store import LimitUpStore
from src.ashare.tasks.market_report import (
    MarketMetrics,
    MarketReportTask,
    ReportKind,
    _collect_metrics,
    _collect_weekly_metrics,
    _first_breakout_targets,
    _previous_trade_date,
    _render_data_appendix,
    _seal_tiers,
    _strong_targets,
)
from src.ashare.tasks.report_llm import _normalize_markdown


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


def test_board_height_distribution(tmp_path) -> None:
    """Metrics should correctly bucket sealed records by consecutive limit-up height."""
    store = LimitUpStore(root=tmp_path / "limit_up")
    store.save(
        [
            _make_record(symbol="F1", limit_up_count=1, close_price=10.0, limit_up_price=10.0),
            _make_record(symbol="F2", limit_up_count=1, close_price=11.0, limit_up_price=11.0),
            _make_record(symbol="S1", limit_up_count=2, close_price=12.0, limit_up_price=12.0),
            _make_record(symbol="T1", limit_up_count=3, close_price=13.0, limit_up_price=13.0),
            _make_record(symbol="FP1", limit_up_count=4, close_price=14.0, limit_up_price=14.0),
            _make_record(symbol="FP2", limit_up_count=5, close_price=15.0, limit_up_price=15.0),
            # broken board should not count in sealed distribution
            _make_record(symbol="B1", limit_up_count=1, close_price=9.0, limit_up_price=10.0),
        ]
    )

    metrics = _collect_metrics(date(2025, 6, 10), store)
    assert metrics.first_board_count == 2
    assert metrics.second_board_count == 1
    assert metrics.third_board_count == 1
    assert metrics.fourth_plus_board_count == 2
    assert metrics.max_limit_up_count == 5


def test_seal_tier_classification() -> None:
    records = [
        _make_record(seal_amount=600_000_000),  # 极强
        _make_record(seal_amount=300_000_000),  # 强
        _make_record(seal_amount=100_000_000),  # 中
        _make_record(seal_amount=10_000_000),  # 弱
    ]
    tiers = _seal_tiers(records)
    assert tiers == {"极强": 1, "强": 1, "中": 1, "弱": 1}


def test_strong_targets_filter_strategy_a() -> None:
    records = [
        # qualifies: sealed, >=3 boards, open_rate <20%, seal >=2亿
        _make_record(
            symbol="S1",
            limit_up_count=3,
            close_price=30.0,
            limit_up_price=30.0,
            open_count=0,
            seal_amount=250_000_000,
        ),
        # fails: open_count too high
        _make_record(
            symbol="S2",
            limit_up_count=4,
            close_price=40.0,
            limit_up_price=40.0,
            open_count=3,
            seal_amount=300_000_000,
        ),
        # fails: only 1 board
        _make_record(
            symbol="S3",
            limit_up_count=1,
            close_price=10.0,
            limit_up_price=10.0,
            open_count=0,
            seal_amount=300_000_000,
        ),
        # fails: not sealed
        _make_record(
            symbol="S4",
            limit_up_count=3,
            close_price=29.0,
            limit_up_price=30.0,
            open_count=0,
            seal_amount=300_000_000,
        ),
    ]
    targets = _strong_targets(records)
    assert len(targets) == 1
    assert targets[0]["symbol"] == "S1"


def test_first_breakout_targets_filter_strategy_b() -> None:
    records = [
        # qualifies
        _make_record(
            symbol="F1",
            limit_up_count=1,
            close_price=10.0,
            limit_up_price=10.0,
            seal_amount=60_000_000,
            industry="半导体",
        ),
        # fails: seal too small
        _make_record(
            symbol="F2",
            limit_up_count=1,
            close_price=11.0,
            limit_up_price=11.0,
            seal_amount=10_000_000,
        ),
        # fails: 2 boards
        _make_record(
            symbol="F3",
            limit_up_count=2,
            close_price=12.0,
            limit_up_price=12.0,
            seal_amount=100_000_000,
        ),
    ]
    targets = _first_breakout_targets(records)
    assert len(targets) == 1
    assert targets[0]["symbol"] == "F1"


def test_weekly_aggregation_across_multiple_days(tmp_path) -> None:
    """Weekly metrics should aggregate records across the full week."""
    store = LimitUpStore(root=tmp_path / "limit_up")
    week_end = date(2025, 6, 13)  # Friday
    week_start = week_end - timedelta(days=week_end.weekday())

    for i, day in enumerate([week_start + timedelta(days=d) for d in range(5)]):
        store.save(
            [
                _make_record(
                    trade_date=day,
                    symbol=f"D{i}A",
                    limit_up_count=1,
                    close_price=10.0 + i,
                    limit_up_price=10.0 + i,
                    seal_amount=100_000_000,
                    industry="半导体",
                ),
                _make_record(
                    trade_date=day,
                    symbol=f"D{i}B",
                    limit_up_count=2,
                    close_price=20.0 + i,
                    limit_up_price=20.0 + i,
                    seal_amount=50_000_000,
                    industry="化学制品",
                ),
            ]
        )

    metrics = _collect_weekly_metrics(week_end, store)
    assert metrics.limit_up_count == 10  # 2 sealed per day * 5 days
    assert metrics.weekly_start == week_start.isoformat()
    assert metrics.weekly_end == week_end.isoformat()
    assert len(metrics.daily_limit_up_counts) == 5
    assert metrics.first_board_count == 5
    assert metrics.second_board_count == 5
    # hot industries should aggregate across the week
    assert len(metrics.hot_industries) == 2


def test_open_report_uses_previous_trading_day_data(tmp_path, monkeypatch) -> None:
    """开盘报告 should be based on the most recent trading day with stored data."""
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    store = LimitUpStore(root=tmp_path / "limit_up")

    prev_day = date(2025, 6, 9)
    report_day = date(2025, 6, 10)

    store.save(
        [
            _make_record(
                trade_date=prev_day,
                symbol="A",
                industry="半导体",
                seal_amount=100_000_000,
                close_price=10.0,
                limit_up_price=10.0,
            ),
        ]
    )

    task = MarketReportTask(store)
    report = task.run_sync(ReportKind.OPEN, report_day)

    assert "A股开盘报告" in report.title
    assert report.trade_date == report_day
    assert "## 热门行业 TOP10" in report.markdown


def test_llm_unconfigured_falls_back_to_data_only(tmp_path, monkeypatch) -> None:
    """When LLM is not configured, the report should still generate with data only."""
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.delenv("LANGCHAIN_MODEL_NAME", raising=False)
    store = LimitUpStore(root=tmp_path / "limit_up")
    store.save(
        [
            _make_record(
                symbol="A",
                industry="半导体",
                seal_amount=100_000_000,
                close_price=10.0,
                limit_up_price=10.0,
            ),
        ]
    )

    task = MarketReportTask(store)
    report = task.run_sync(ReportKind.CLOSE, date(2025, 6, 10))

    assert report.metrics["llm"]["used"] is False
    assert "结构化市场数据" in report.markdown or "数据驱动" in report.markdown or "摘要" in report.markdown
    assert "## 涨停概览" in report.markdown


def test_data_appendix_contains_extended_sections(tmp_path) -> None:
    """The data appendix should include all newly-added sections."""
    store = LimitUpStore(root=tmp_path / "limit_up")
    store.save(
        [
            _make_record(
                symbol="A",
                limit_up_count=1,
                close_price=10.0,
                limit_up_price=10.0,
                seal_amount=100_000_000,
                industry="半导体",
            ),
        ]
    )

    metrics = _collect_metrics(date(2025, 6, 10), store)
    appendix = _render_data_appendix(metrics)
    assert "## 主要指数行情" in appendix
    assert "## 涨停概览" in appendix
    assert "### 连板高度分布" in appendix
    assert "## 封单强度分布" in appendix
    assert "## 龙头追踪" in appendix
    assert "## 热门行业 TOP10" in appendix


def test_previous_trade_date_finds_latest_day_with_data(tmp_path) -> None:
    store = LimitUpStore(root=tmp_path / "limit_up")
    store.save([_make_record(trade_date=date(2025, 6, 6), symbol="A")])
    store.save([_make_record(trade_date=date(2025, 6, 9), symbol="B")])

    found = _previous_trade_date(date(2025, 6, 10), store)
    assert found == date(2025, 6, 9)


# Helpers attached to the task class for easier synchronous testing.
def _run_sync(self, kind: ReportKind, trade_date: date | None = None):
    import asyncio

    return asyncio.run(self.run(kind, trade_date))


MarketReportTask.run_sync = _run_sync  # type: ignore[attr-defined]


def test_normalize_markdown_strips_think_blocks_and_fences() -> None:
    """Internal reasoning tags and markdown fences must be removed."""
    raw = "<think>\n我需要分析...\n</think>\n# 核心观点\n市场强势。"
    assert _normalize_markdown(raw) == "# 核心观点\n市场强势。"

    raw2 = "```markdown\n<think>reasoning</think>\n# 摘要\n...\n```"
    assert _normalize_markdown(raw2) == "# 摘要\n..."
