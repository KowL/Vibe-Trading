"""In-memory store for strategy market snapshots.

Snapshots are keyed by strategy_id.  The store is intentionally simple: the
strategy market is a real-time view and can be rebuilt on server restart by
re-running the registered strategies.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.ashare.strategies.market_models import StrategyDefinition, StrategySnapshot

logger = logging.getLogger(__name__)


class StrategyMarketStore:
    """Thread-safe-ish in-memory store for the strategy market.

    All mutations happen inside the market engine, which serialises writes
    through an asyncio lock.
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, StrategySnapshot] = {}
        self._last_updated: datetime | None = None

    def update(self, snapshot: StrategySnapshot) -> None:
        """Store or replace a snapshot."""
        self._snapshots[snapshot.strategy_id] = snapshot
        self._last_updated = datetime.now()

    def get(self, strategy_id: str) -> StrategySnapshot | None:
        """Return a snapshot by strategy id, or None."""
        return self._snapshots.get(strategy_id)

    def get_all(self) -> dict[str, StrategySnapshot]:
        """Return a shallow copy of the snapshot map."""
        return dict(self._snapshots)

    def remove(self, strategy_id: str) -> bool:
        """Remove a snapshot. Returns True if it existed."""
        existed = strategy_id in self._snapshots
        if existed:
            del self._snapshots[strategy_id]
        return existed

    @property
    def last_updated(self) -> datetime | None:
        return self._last_updated

    def to_state(self, definitions: list[StrategyDefinition]) -> dict[str, Any]:
        """Serialize current market state for API consumers."""
        return {
            "strategies": [d.model_dump() for d in definitions],
            "snapshots": {
                sid: snap.model_dump() for sid, snap in self._snapshots.items()
            },
            "last_updated": self._last_updated.isoformat() if self._last_updated else None,
        }
