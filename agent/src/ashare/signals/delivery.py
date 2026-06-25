"""Top-level entry point for the signal delivery subsystem.

The :class:`SignalDeliveryService` is the single object every strategy
runner interacts with. Internally it owns:

* the deduplicator (in-process, sliding window);
* the audit log (append-only JSONL);
* the ordered list of sinks (fan-out target).

Pipeline (per :meth:`deliver`):

1. ``audit.write_received(signal)`` — unconditional, so deduped signals
   are still traceable.
2. ``dedup.should_emit(signal)`` — return ``DeliveryResult(deduped=True)``
   on miss, after writing ``signal_deduped`` to the audit log.
3. Fan out to all sinks via :func:`asyncio.gather` with
   ``return_exceptions=True`` so one failing sink does not stop the
   others. Each sink swallows its own errors (see
   :class:`~src.ashare.signals.sinks.base.SignalSink`); the service
   records the failure by sink name in ``DeliveryResult.failed_to``.
4. ``audit.write_emitted(signal, delivered_to=, failed_to=)``.

A module-level factory (:func:`get_delivery_service`) returns a lazily
initialized process-wide singleton so runners do not need to know how
sinks are wired. The singleton can be reset in tests via
:func:`reset_delivery_service`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any, Sequence

from src.ashare.signals.audit import SignalAuditLog
from src.ashare.signals.dedup import SignalDeduplicator
from src.ashare.signals.models import DeliveryResult, NormalizedSignal
from src.ashare.signals.sinks.base import SignalSink

logger = logging.getLogger(__name__)


class SignalDeliveryService:
    """Fan-out service for one :class:`NormalizedSignal`.

    Args:
        sinks: Ordered list of delivery targets. Order is not
            semantically meaningful (sinks run concurrently), but
            ``audit.delivered_to`` preserves the order in which
            successful sinks report.
        dedup: Deduplicator. Defaults to a 30-minute-window one.
        audit: Audit log. Defaults to the on-disk JSONL at
            ``~/.vibe-trading/ashare/audit/signals.jsonl``.
    """

    def __init__(
        self,
        sinks: Sequence[SignalSink],
        dedup: SignalDeduplicator | None = None,
        audit: SignalAuditLog | None = None,
    ) -> None:
        self._sinks: list[SignalSink] = list(sinks)
        self._dedup = dedup or SignalDeduplicator()
        self._audit = audit or SignalAuditLog()

    # ------------------------------------------------------------------ #
    # public API                                                         #
    # ------------------------------------------------------------------ #

    async def deliver(self, signal: NormalizedSignal) -> DeliveryResult:
        """Run the full audit → dedup → fan-out pipeline for one signal.

        Returns a :class:`DeliveryResult` describing the outcome. The
        result is always non-error: dedup or fan-out failure is
        reported via the ``deduped`` / ``failed_to`` fields, never by
        raising. This keeps the strategy runner's hot path simple.
        """
        # 1. Unconditional "received" audit (SPEC §3.3).
        self._audit.write_received(signal)

        # 2. Dedup gate.
        if not self._dedup.should_emit(signal):
            self._audit.write_deduped(signal, reason="cooldown")
            return DeliveryResult(delivered_to=[], deduped=True)

        # 3. Fan out: every sink runs concurrently; one failure does
        #    not stop the others. The ``return_exceptions=True`` makes
        #    ``gather`` collect per-task results instead of raising.
        results = await asyncio.gather(
            *(sink.send(signal) for sink in self._sinks),
            return_exceptions=True,
        )
        delivered_to: list[str] = []
        failed_to: list[str] = []
        for sink, result in zip(self._sinks, results):
            if isinstance(result, BaseException):
                failed_to.append(sink.name())
                logger.warning(
                    "sink %s raised for %s %s: %r",
                    sink.name(), signal.symbol, signal.side, result,
                )
            else:
                delivered_to.append(sink.name())

        # 4. Record the post-fan-out state.
        self._audit.write_emitted(
            signal, delivered_to=delivered_to, failed_to=failed_to,
        )
        return DeliveryResult(delivered_to=delivered_to, failed_to=failed_to)

    async def deliver_for_symbol(
        self,
        *,
        strategy_id: str,
        market_date: date,
        symbol: str,
        side: str,
        ref_price: float,
        confidence: float,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> DeliveryResult:
        """One-call convenience for runners that emit per-symbol signals.

        Equivalent to constructing a :class:`NormalizedSignal` and
        calling :meth:`deliver`. Designed for ``asyncio.create_task``
        from inside a multi-symbol runner (e.g. the bollinger strategy
        iterating over a watchlist).
        """
        signal = NormalizedSignal(
            strategy_id=strategy_id,
            market_date=market_date,
            ts=datetime.now(),
            symbol=symbol,
            side=side,
            ref_price=ref_price,
            confidence=confidence,
            reason=reason,
            metadata=metadata or {},
        )
        return await self.deliver(signal)

    def stats(self) -> dict[str, Any]:
        """Return small health/diagnostic stats."""
        return {
            "sinks": [s.name() for s in self._sinks],
            "dedup": self._dedup.stats(),
        }


# --------------------------------------------------------------------------- #
# Module-level singleton                                                     #
# --------------------------------------------------------------------------- #

_service: SignalDeliveryService | None = None


def get_default_sinks() -> list[SignalSink]:
    """Build the default sink list from the YAML config.

    Default list (SPEC §1.3):
      - LocalSink   (always; one local JSON file per strategy per day)
      - SSESink     (always; frontend can subscribe to the SSE bus)
      - WebhookSink (only when ``sinks.webhook.enabled`` is true and at
        least one enabled provider is configured; SPEC §5.1)

    Loading the YAML is done here (not at import time) so a config
    reload does not require re-importing the package. The webhook
    sink is built only when configured; otherwise the service carries
    the same two sinks the user has been running.

    Webhook construction never raises: a malformed or empty provider
    list silently degrades to no webhook sink. Errors during
    :func:`load_signals_config` are logged and the function falls back
    to the built-in defaults.
    """
    from src.ashare.signals.config import load_signals_config
    from src.ashare.signals.sinks.local_sink import LocalSink
    from src.ashare.signals.sinks.sse_sink import SSESink
    from src.ashare.signals.sinks.webhook_sink import WebhookSink

    sinks: list[SignalSink] = [LocalSink(), SSESink()]

    try:
        cfg = load_signals_config()
    except Exception as exc:  # noqa: BLE001 - bad config must not break the service
        logger.warning("signals config load failed: %s; webhook sink disabled", exc)
        return sinks

    if not cfg.sinks.webhook.enabled:
        return sinks
    enabled_providers = [p for p in cfg.sinks.webhook.providers if p.enabled]
    if not enabled_providers:
        return sinks
    sinks.append(WebhookSink(enabled_providers))
    return sinks


def get_delivery_service() -> SignalDeliveryService:
    """Return the process-wide :class:`SignalDeliveryService`.

    The first call wires up the default sinks (LocalSink + SSESink).
    Subsequent calls return the same instance. To swap sinks (e.g.
    in tests, or after a future config reload) call
    :func:`reset_delivery_service` first.
    """
    global _service
    if _service is None:
        _service = SignalDeliveryService(sinks=get_default_sinks())
    return _service


def reset_delivery_service() -> None:
    """Drop the cached singleton. Used by tests and by config reloads.

    The next :func:`get_delivery_service` call rebuilds a fresh
    :class:`SignalDeliveryService` with the current default sinks.
    """
    global _service
    _service = None
