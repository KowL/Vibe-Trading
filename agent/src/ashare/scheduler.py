"""A-share scheduled task runner (lightweight wrapper over live runtime scheduler).

Ports Ruo.ai's Celery tasks to Vibe-Trading's asyncio scheduler using a
simple file-lock guard: each task writes a lock file after success, and
skips if the lock already exists for the current calendar day.

Time windows (Shanghai TZ):
    09:00-09:30  market_report_open
    15:30-16:00  limit_up_sync
    18:00-18:30  market_report_close
    Friday 19:00-19:30  market_report_weekly
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from zoneinfo import ZoneInfo

from src.ashare.tasks.limit_up_sync import LimitUpSyncResult, LimitUpSyncTask
from src.ashare.tasks.market_report import MarketReportTask, ReportKind
from src.live.runtime.scheduler import Job, Scheduler
from src.live.runtime.jobstore import JobStore

logger = logging.getLogger(__name__)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_LOCK_SUBDIR = "ashare/locks"


# --------------------------------------------------------------------------- #
# Time-window configuration                                                   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _TimeWindow:
    start: time
    end: time
    weekdays: set[int] | None = None  # None = all weekdays


_TASK_WINDOWS: dict[str, _TimeWindow] = {
    "market_report_open": _TimeWindow(time(9, 0), time(9, 30)),
    "limit_up_sync": _TimeWindow(time(15, 30), time(16, 0)),
    "market_report_close": _TimeWindow(time(18, 0), time(18, 30)),
    "market_report_weekly": _TimeWindow(time(19, 0), time(19, 30), weekdays={4}),  # Friday
    "strategy_market_refresh": _TimeWindow(time(16, 0), time(16, 30)),
}


# --------------------------------------------------------------------------- #
# Lock helpers                                                                #
# --------------------------------------------------------------------------- #


def _locks_dir() -> Path:
    d = Path.home() / ".vibe-trading" / _LOCK_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _lock_path(task_id: str, day: date) -> Path:
    return _locks_dir() / f"{task_id}_{day.isoformat()}"


def _is_locked(task_id: str, day: date | None = None) -> bool:
    if day is None:
        day = _today_shanghai()
    return _lock_path(task_id, day).exists()


def _mark_locked(task_id: str, day: date | None = None) -> Path:
    if day is None:
        day = _today_shanghai()
    path = _lock_path(task_id, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def _clear_lock(task_id: str, day: date | None = None) -> None:
    if day is None:
        day = _today_shanghai()
    path = _lock_path(task_id, day)
    if path.exists():
        path.unlink()


def _today_shanghai() -> date:
    return datetime.now(_SHANGHAI).date()


def _now_shanghai() -> datetime:
    return datetime.now(_SHANGHAI)


# --------------------------------------------------------------------------- #
# Time-window check                                                           #
# --------------------------------------------------------------------------- #


def _in_window(task_id: str, now: datetime | None = None) -> bool:
    """Return True if now falls inside the task's configured time window."""
    if now is None:
        now = _now_shanghai()
    window = _TASK_WINDOWS.get(task_id)
    if window is None:
        return True  # no window = always allowed

    # weekday filter
    if window.weekdays is not None and now.weekday() not in window.weekdays:
        return False

    current = now.time()
    return window.start <= current < window.end


# --------------------------------------------------------------------------- #
# Task dispatcher                                                             #
# --------------------------------------------------------------------------- #


class AShareTaskRunner:
    """Stateful runner that dispatches A-share tasks and guards against re-runs."""

    def __init__(
        self,
        limit_up_task: LimitUpSyncTask | None = None,
        report_task: MarketReportTask | None = None,
    ) -> None:
        self.limit_up_task = limit_up_task or LimitUpSyncTask()
        self.report_task = report_task or MarketReportTask()

    async def dispatch(self, task_id: str) -> Any:
        """Run a task if its window is open and it hasn't run today.

        Returns the task result, or None if skipped.
        """
        today = _today_shanghai()

        # 1. Already ran today?
        if _is_locked(task_id, today):
            logger.debug("%s already ran on %s — skipping", task_id, today)
            return None

        # 2. Inside time window?
        if not _in_window(task_id):
            logger.debug("%s outside time window — skipping", task_id)
            return None

        # 3. Execute
        logger.info("running %s", task_id)
        try:
            result = await self._run(task_id)
        except Exception:
            logger.error("%s failed", task_id, exc_info=True)
            raise

        # 4. Mark done
        _mark_locked(task_id, today)
        logger.info("%s completed on %s", task_id, today)
        return result

    async def _run(self, task_id: str) -> Any:
        if task_id == "limit_up_sync":
            result = await self.limit_up_task.run()
            # Publish SSE event
            from src.ashare.live_publisher import get_publisher
            pub = get_publisher()
            if hasattr(result, 'trade_date') and hasattr(result, 'count'):
                pub.publish_limit_up_sync(result.trade_date, result.count, result.source)
            return result
        if task_id == "strategy_market_refresh":
            from src.ashare.strategies.market_engine import get_market_engine
            engine = get_market_engine()
            results = await engine.refresh_all()
            return {"refreshed": list(results.keys()), "count": len(results)}
        if task_id == "market_report_open":
            result = await self.report_task.run(ReportKind.OPEN)
            self._publish_report(result, "open")
            return result
        if task_id == "market_report_close":
            result = await self.report_task.run(ReportKind.CLOSE)
            self._publish_report(result, "close")
            return result
        if task_id == "market_report_weekly":
            result = await self.report_task.run(ReportKind.WEEKLY)
            self._publish_report(result, "weekly")
            return result
        raise ValueError(f"unknown task: {task_id}")

    def _publish_report(self, result: Any, kind: str) -> None:
        """Publish report generation event."""
        from src.ashare.live_publisher import get_publisher
        pub = get_publisher()
        if hasattr(result, 'trade_date') and hasattr(result, 'title'):
            pub.publish_market_report(kind, result.trade_date, result.title)


# --------------------------------------------------------------------------- #
# Scheduler integration                                                       #
# --------------------------------------------------------------------------- #


def _build_jobs() -> list[Job]:
    """Create the four A-share scheduled jobs.

    Each job uses a MARKET trigger on china_a_share so the scheduler wakes
    only when the A-share market is open.  The task runner's time-window
    guard ensures each task fires at most once per day inside its slot.
    """
    now_ms = int(datetime.now(_SHANGHAI).timestamp() * 1000)
    # Use a 60-second interval so the runner re-checks frequently during
    # market hours without hammering the CPU.
    return [
        Job(
            id="ashare_market_report_open",
            next_run_at=now_ms,
            schedule="interval:60000",
            payload={"task": "market_report_open"},
        ),
        Job(
            id="ashare_limit_up_sync",
            next_run_at=now_ms,
            schedule="interval:60000",
            payload={"task": "limit_up_sync"},
        ),
        Job(
            id="ashare_market_report_close",
            next_run_at=now_ms,
            schedule="interval:60000",
            payload={"task": "market_report_close"},
        ),
        Job(
            id="ashare_market_report_weekly",
            next_run_at=now_ms,
            schedule="interval:60000",
            payload={"task": "market_report_weekly"},
        ),
        Job(
            id="ashare_strategy_market_refresh",
            next_run_at=now_ms,
            schedule="interval:60000",
            payload={"task": "strategy_market_refresh"},
        ),
    ]


class AShareScheduler:
    """Lightweight wrapper that pairs the live Scheduler with AShareTaskRunner.

    Lifecycle:
        1. Call start() on API server boot.
        2. The scheduler wakes every 60s during market hours.
        3. on_fire delegates to AShareTaskRunner.dispatch().
        4. Tasks are skipped if already run today or outside their window.
    """

    def __init__(
        self,
        runner: AShareTaskRunner | None = None,
        scheduler: Scheduler | None = None,
        job_store: JobStore | None = None,
    ) -> None:
        self.runner = runner or AShareTaskRunner()
        self._scheduler = scheduler
        self._job_store = job_store or JobStore()

    async def _on_fire(self, job: Job) -> None:
        """Callback invoked by the live scheduler for each due job."""
        task_id = job.payload.get("task") if job.payload else None
        if not task_id:
            logger.warning("job %s has no task payload", job.id)
            return
        await self.runner.dispatch(task_id)

    def start(self) -> None:
        """Load persisted jobs (or create defaults), start the scheduler loop."""
        # Try to load existing jobs; fall back to building defaults.
        try:
            jobs = self._job_store.load()
        except Exception:
            jobs = []

        # Filter to A-share jobs only; rebuild if none exist.
        ashare_jobs = [j for j in jobs if j.id.startswith("ashare_")]
        if not ashare_jobs:
            ashare_jobs = _build_jobs()
            self._job_store.save(ashare_jobs)
            logger.info("created default A-share jobs")

        # Inject or rebuild the scheduler with our on_fire callback.
        self._scheduler = Scheduler(on_fire=self._on_fire)
        for job in ashare_jobs:
            self._scheduler.add_job(job)
        self._scheduler.start()
        logger.info("A-share scheduler started with %d jobs", len(ashare_jobs))

    async def stop(self) -> None:
        """Stop the scheduler and persist the job set."""
        if self._scheduler is not None:
            await self._scheduler.stop()
            self._job_store.save(self._scheduler.jobs())
            logger.info("A-share scheduler stopped")

    def trigger_now(self, task_id: str) -> asyncio.Task:
        """Manually trigger a task bypassing the time-window guard (for testing / API)."""
        return asyncio.create_task(self.runner.dispatch(task_id))
