"""In-process signal deduplicator.

A :class:`SignalDeduplicator` blocks re-delivery of the same
``(strategy_id, symbol, side)`` triple within a sliding window. It is
intentionally simple (in-memory dict + a monotonic clock) so the signal
runner can call :meth:`should_emit` cheaply on the hot path. Persistence
across restarts is deliberately *not* implemented: signals emitted while
the process was down are simply lost, which matches the SPEC's "research
signals, not order intents" framing (SPEC §1.2 non-goal 1).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque

from src.ashare.signals.models import NormalizedSignal

logger = logging.getLogger(__name__)


@dataclass
class _Recent:
    """A small struct recording when a key was last emitted."""

    last_ts: float
    n: int = 1


class SignalDeduplicator:
    """Cooldown-window deduplicator keyed on ``signal.dedup_key()``.

    Args:
        cooldown_seconds: How long a key stays "warm" after emission.
            Same key seen again inside the window is dropped.
        max_keys: Hard cap on the in-memory key set. Older entries are
            evicted (FIFO) when the cap is hit so the dedup dict cannot
            grow without bound across long-running processes. Defaults
            to 4096, which covers ~2 trading days at 200 signals/day.
        clock: Injected ``() -> float`` returning monotonic seconds.
            Tests can pass a fake clock for determinism.

    Thread-safety:
        All public methods are safe under single-process asyncio
        concurrency. They are NOT safe under multi-threaded callers;
        wrap in a lock if you must share across threads.
    """

    def __init__(
        self,
        cooldown_seconds: int = 1800,
        max_keys: int = 4096,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")
        self._cooldown = float(cooldown_seconds)
        self._clock = clock or time.monotonic
        self._entries: dict[tuple[str, str, str], _Recent] = {}
        self._insertion_order: Deque[tuple[str, str, str]] = deque(maxlen=max_keys if max_keys > 0 else None)

    def should_emit(self, signal: NormalizedSignal) -> bool:
        """Return True if ``signal`` should be delivered now.

        Side-effect: when returning True, the dedup entry is refreshed
        so subsequent calls within the window return False. When
        returning False, no state is mutated.
        """
        key = signal.dedup_key()
        now = self._clock()
        recent = self._entries.get(key)
        if recent is not None and (now - recent.last_ts) < self._cooldown:
            return False
        if recent is None:
            self._evict_if_full()
            self._insertion_order.append(key)
        else:
            recent.last_ts = now
            recent.n += 1
            return True
        self._entries[key] = _Recent(last_ts=now, n=1)
        return True

    def clear(self) -> None:
        """Drop all dedup state. Useful in tests; not exposed in CLI."""
        self._entries.clear()
        self._insertion_order.clear()

    def stats(self) -> dict[str, int]:
        """Return a small stats dict for /healthz endpoints or logs."""
        return {
            "tracked_keys": len(self._entries),
            "max_keys": len(self._insertion_order),
        }

    # ------------------------------------------------------------------ #
    # internal                                                           #
    # ------------------------------------------------------------------ #

    def _evict_if_full(self) -> None:
        """FIFO-evict the oldest key when the cap is hit. O(1)."""
        cap = self._cap()
        if cap is None:
            return
        while len(self._insertion_order) >= cap:
            old_key = self._insertion_order.popleft()
            self._entries.pop(old_key, None)
            logger.debug("dedup evicted key=%s (cap=%d)", old_key, cap)

    def _cap(self) -> int | None:
        """Resolve the cap from the public attr or the deque maxlen."""
        # The deque's maxlen is the source of truth; ``_cap`` is here so
        # future custom sizing (e.g. by config) can be plugged in without
        # touching the eviction call sites.
        return self._insertion_order.maxlen
