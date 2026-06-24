"""A-share real-time data publisher via SSE.

Pushes limit-up updates to connected Web UI clients.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from typing import Any

from src.ashare.storage.limit_up_store import LimitUpStore
from src.session.events import EventBus, SSEEvent

logger = logging.getLogger(__name__)


class AShareLivePublisher:
    """Publish A-share market events to the SSE event bus.

    Used by the scheduler to push real-time updates to Web UI clients.
    """

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self.event_bus = event_bus
        self.store = LimitUpStore()
        self._last_published_count: dict[str, int] = {}

    def set_event_bus(self, event_bus: EventBus) -> None:
        """Set the event bus (called during api_server startup)."""
        self.event_bus = event_bus

    def publish_limit_up_sync(self, trade_date: date, count: int, source: str) -> None:
        """Publish limit-up sync completion event."""
        if not self.event_bus:
            return
        self.event_bus.emit(
            session_id="ashare_broadcast",
            event_type="ashare_limit_up_sync",
            data={
                "trade_date": trade_date.isoformat(),
                "count": count,
                "source": source,
                "timestamp": datetime.now().isoformat(),
            },
        )
        logger.info("Published limit-up sync: %s %d records", trade_date, count)

    def publish_market_report(self, kind: str, trade_date: date, title: str) -> None:
        """Publish market report generation event."""
        if not self.event_bus:
            return
        self.event_bus.emit(
            session_id="ashare_broadcast",
            event_type="ashare_market_report",
            data={
                "kind": kind,
                "trade_date": trade_date.isoformat(),
                "title": title,
                "timestamp": datetime.now().isoformat(),
            },
        )
        logger.info("Published market report: %s %s", kind, trade_date)

    def publish_scheduler_heartbeat(self, jobs: list[dict[str, Any]]) -> None:
        """Publish scheduler heartbeat with active jobs."""
        if not self.event_bus:
            return
        self.event_bus.emit(
            session_id="ashare_broadcast",
            event_type="ashare_scheduler_heartbeat",
            data={
                "jobs": jobs,
                "timestamp": datetime.now().isoformat(),
            },
        )

    def publish_strategy_market(self, snapshot: Any) -> None:
        """Publish a strategy market snapshot update."""
        if not self.event_bus:
            return
        try:
            data = snapshot.model_dump() if hasattr(snapshot, "model_dump") else dict(snapshot)
        except Exception:
            data = {"strategy_id": getattr(snapshot, "strategy_id", "unknown")}
        self.event_bus.emit(
            session_id="ashare_broadcast",
            event_type="ashare_strategy_market",
            data={
                "snapshot": data,
                "timestamp": datetime.now().isoformat(),
            },
        )
        logger.info("Published strategy market snapshot: %s", data.get("strategy_id"))


# Global singleton (set by api_server on startup)
_ashare_publisher: AShareLivePublisher | None = None


def get_publisher() -> AShareLivePublisher:
    """Get the global AShareLivePublisher instance."""
    global _ashare_publisher
    if _ashare_publisher is None:
        _ashare_publisher = AShareLivePublisher()
    return _ashare_publisher


def set_publisher(publisher: AShareLivePublisher) -> None:
    """Set the global publisher (called by api_server)."""
    global _ashare_publisher
    _ashare_publisher = publisher
