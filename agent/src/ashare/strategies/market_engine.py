"""Strategy market engine.

Orchestrates running registered strategies, caching their snapshots, and
publishing updates to the SSE bus.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

from src.ashare.strategies.market_models import (
    StrategyDefinition,
    StrategyMarketState,
    StrategyRunRequest,
    StrategySnapshot,
)
from src.ashare.strategies.market_store import StrategyMarketStore
from src.ashare.strategies import strategy_registry
from src.ashare.strategies import market_runner  # noqa: F401  # registers runners

logger = logging.getLogger(__name__)


class StrategyMarketEngine:
    """Runs strategies and keeps the market store up to date."""

    def __init__(
        self,
        store: StrategyMarketStore | None = None,
        max_concurrent: int = 2,
    ) -> None:
        self.store = store or StrategyMarketStore()
        self._max_concurrent = max_concurrent
        self._lock = asyncio.Lock()
        self._publisher: Any | None = None

    def set_publisher(self, publisher: Any) -> None:
        """Attach the live publisher for SSE events."""
        self._publisher = publisher

    def catalogue(self) -> list[StrategyDefinition]:
        """Return market-visible strategy definitions."""
        return strategy_registry.list_market_definitions()

    def strategy_ids(self) -> list[str]:
        """Return all registered strategy ids."""
        return strategy_registry.list_strategy_ids()

    def market_strategy_ids(self) -> list[str]:
        """Return market-visible strategy ids."""
        return strategy_registry.list_market_strategy_ids()

    async def refresh(
        self,
        strategy_id: str,
        market_date: date | None = None,
        params: dict[str, Any] | None = None,
        run_backtest: bool = True,
    ) -> StrategySnapshot:
        """Run a single strategy and cache its snapshot.

        The sync runner is executed in a thread pool so the event loop stays
        responsive.
        """
        runner = strategy_registry.get_runner(strategy_id)
        request = StrategyRunRequest(
            strategy_id=strategy_id,
            market_date=market_date,
            params=params or {},
            run_backtest=run_backtest,
        )

        # Mark running while the computation is in flight
        running = StrategySnapshot(
            strategy_id=strategy_id,
            run_at=datetime.now(),
            status="running",
            market_date=market_date,
            matched=[],
        )
        async with self._lock:
            self.store.update(running)

        try:
            snapshot = await asyncio.to_thread(runner, request)
        except Exception as exc:
            logger.exception("strategy %s runner failed", strategy_id)
            snapshot = StrategySnapshot(
                strategy_id=strategy_id,
                run_at=datetime.now(),
                status="error",
                market_date=market_date,
                matched=[],
                error=str(exc),
            )

        async with self._lock:
            self.store.update(snapshot)

        self._publish(snapshot)
        return snapshot

    async def refresh_all(
        self,
        market_date: date | None = None,
        params: dict[str, Any] | None = None,
        run_backtest: bool = True,
    ) -> dict[str, StrategySnapshot]:
        """Refresh every market-visible strategy.

        Signal-delivery strategies that are not market-visible are skipped;
        they are refreshed by their own scheduler jobs. A semaphore limits
        how many CPU-heavy backtests run at once.
        """
        semaphore = asyncio.Semaphore(self._max_concurrent)

        async def _run_one(strategy_id: str) -> StrategySnapshot:
            async with semaphore:
                return await self.refresh(
                    strategy_id,
                    market_date=market_date,
                    params=params,
                    run_backtest=run_backtest,
                )

        results = await asyncio.gather(
            *[_run_one(sid) for sid in self.market_strategy_ids()]
        )
        return {snap.strategy_id: snap for snap in results}

    def get_state(self) -> StrategyMarketState:
        """Return the current market state.

        Only market-visible strategies and their snapshots are exposed to the
        strategy-market UI; hidden signal-delivery strategies are kept internal.
        """
        visible_ids = set(self.market_strategy_ids())
        all_snapshots = self.store.get_all()
        return StrategyMarketState(
            strategies=self.catalogue(),
            snapshots={
                sid: snap for sid, snap in all_snapshots.items() if sid in visible_ids
            },
            last_updated=self.store.last_updated,
        )

    def get_snapshot(self, strategy_id: str) -> StrategySnapshot | None:
        """Return the cached snapshot for a strategy."""
        return self.store.get(strategy_id)

    def _publish(self, snapshot: StrategySnapshot) -> None:
        """Publish a strategy market event if a publisher is attached."""
        if self._publisher is None:
            return
        try:
            if hasattr(self._publisher, "publish_strategy_market"):
                self._publisher.publish_strategy_market(snapshot)
        except Exception:
            logger.exception("failed to publish strategy market event")


# Global singleton used by the API server and scheduler.
_market_engine: StrategyMarketEngine | None = None


def get_market_engine() -> StrategyMarketEngine:
    """Get or create the global strategy market engine."""
    global _market_engine
    if _market_engine is None:
        _market_engine = StrategyMarketEngine()
    return _market_engine


def set_market_engine(engine: StrategyMarketEngine) -> None:
    """Set the global engine (useful for tests)."""
    global _market_engine
    _market_engine = engine
