"""Tests for A-share scheduler (lock guards + time windows)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.ashare.scheduler import (
    _in_window,
    _is_locked,
    _lock_path,
    _mark_locked,
    _clear_lock,
    _TASK_WINDOWS,
    AShareTaskRunner,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_lock_path_format(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "src.ashare.scheduler._locks_dir", lambda: tmp_path / "locks"
    )
    path = _lock_path("limit_up_sync", date(2025, 1, 2))
    assert path.name == "limit_up_sync_2025-01-02"


def test_mark_and_check_lock(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "src.ashare.scheduler._locks_dir", lambda: tmp_path / "locks"
    )
    day = date(2025, 1, 2)
    assert _is_locked("limit_up_sync", day) is False
    _mark_locked("limit_up_sync", day)
    assert _is_locked("limit_up_sync", day) is True
    _clear_lock("limit_up_sync", day)
    assert _is_locked("limit_up_sync", day) is False


def test_open_window_inside() -> None:
    dt = datetime(2025, 1, 2, 9, 15, tzinfo=_SHANGHAI)
    assert _in_window("market_report_open", dt) is True


def test_open_window_outside() -> None:
    dt = datetime(2025, 1, 2, 10, 0, tzinfo=_SHANGHAI)
    assert _in_window("market_report_open", dt) is False


def test_sync_window_inside() -> None:
    dt = datetime(2025, 1, 2, 15, 35, tzinfo=_SHANGHAI)
    assert _in_window("limit_up_sync", dt) is True


def test_sync_window_outside() -> None:
    dt = datetime(2025, 1, 2, 15, 0, tzinfo=_SHANGHAI)
    assert _in_window("limit_up_sync", dt) is False


def test_weekly_only_on_friday() -> None:
    friday = datetime(2025, 1, 3, 19, 15, tzinfo=_SHANGHAI)  # Friday
    saturday = datetime(2025, 1, 4, 19, 15, tzinfo=_SHANGHAI)  # Saturday
    assert _in_window("market_report_weekly", friday) is True
    assert _in_window("market_report_weekly", saturday) is False


def test_weekly_window_end_exclusive() -> None:
    friday_late = datetime(2025, 1, 3, 19, 30, tzinfo=_SHANGHAI)
    assert _in_window("market_report_weekly", friday_late) is False


def test_unknown_task_no_window() -> None:
    assert _in_window("unknown_task", datetime(2025, 1, 2, 12, 0, tzinfo=_SHANGHAI)) is True


def test_runner_skips_when_locked(tmp_path, monkeypatch) -> None:
    """If lock exists, dispatch should return None (skip)."""
    monkeypatch.setattr(
        "src.ashare.scheduler._locks_dir", lambda: tmp_path / "locks"
    )
    _mark_locked("market_report_open", _today_shanghai())

    runner = AShareTaskRunner()
    # dispatch is async; run it via asyncio.run in a sync test
    import asyncio
    result = asyncio.run(runner.dispatch("market_report_open"))
    assert result is None


def _today_shanghai() -> date:
    return datetime.now(_SHANGHAI).date()
