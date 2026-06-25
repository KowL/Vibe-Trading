"""Push :class:`NormalizedSignal` records to the project's SSE event bus.

Reuses the existing :class:`src.session.events.EventBus` via
:func:`src.ashare.live_publisher.get_publisher` — the same channel
(``session_id="ashare_broadcast"``) the existing
:class:`AShareLivePublisher` already uses for limit-up and market
report events. Frontend code that already subscribes to
``ashare_signal_new`` events needs no changes.

If the event bus has not been wired (e.g. when the signal service is
imported in a unit test or by a CLI tool that does not start the API
server), the sink degrades to a no-op: the signal is still recorded
by the audit log and the local file sink, and the user-facing delivery
keeps working.
"""

from __future__ import annotations

import logging

from src.ashare.signals.models import NormalizedSignal

logger = logging.getLogger(__name__)

# Match the existing convention from src/ashare/live_publisher.py so
# frontend subscribers and the API SSE route can keep filtering on
# this session id without code changes.
_BROADCAST_SESSION = "ashare_broadcast"

_EVENT_NEW = "ashare_signal_new"
_EVENT_DEDUPED = "ashare_signal_deduped"


class SSESink:
    """Push signals onto the project's ``ashare_broadcast`` channel.

    Safe to instantiate before the API server wires up the event bus:
    subsequent ``send`` calls will simply no-op with a debug log line
    instead of raising. The bus is resolved lazily on every send so
    wiring it up later (e.g. in ``api_server.py`` startup) just works.
    """

    def name(self) -> str:
        return "sse"

    async def send(self, signal: NormalizedSignal) -> None:
        """Emit one ``ashare_signal_new`` event for ``signal``.

        The event payload mirrors the SPEC §3.4 shape so the frontend
        can render the same way it already renders
        ``ashare_limit_up_sync`` / ``ashare_market_report`` events.

        Errors from the event bus (full queue, etc.) are swallowed
        with a warning — SSE failures MUST NOT break other sinks
        (see :class:`SignalSink` contract).
        """
        try:
            bus = self._resolve_bus()
        except (ImportError, AttributeError) as exc:
            logger.debug("sse sink: bus unavailable, dropping event: %s", exc)
            return
        if bus is None:
            logger.debug("sse sink: no event bus wired, dropping event for %s", signal.symbol)
            return
        try:
            bus.emit(
                session_id=_BROADCAST_SESSION,
                event_type=_EVENT_NEW,
                data=_signal_to_payload(signal),
            )
        except Exception as exc:  # noqa: BLE001 - bus failures must not escape the sink
            logger.warning("sse sink: emit failed for %s: %s", signal.symbol, exc)

    # ------------------------------------------------------------------ #
    # internals                                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_bus():
        """Return the wired event bus, or ``None`` if not yet attached.

        Imports are deferred to avoid pulling the entire session
        package (and the asyncio loop) at module import time. This
        keeps the signal subsystem importable from scripts and tests
        that do not start the API server.
        """
        from src.ashare.live_publisher import get_publisher

        pub = get_publisher()
        return getattr(pub, "event_bus", None)


def _signal_to_payload(signal: NormalizedSignal) -> dict[str, object]:
    """Project :class:`NormalizedSignal` into the SSE event payload.

    Keys are intentionally short (camelCase or single-word) because
    the JSON is shipped over the wire to every connected browser.
    """
    return {
        "strategyId": signal.strategy_id,
        "symbol": signal.symbol,
        "side": signal.side,
        "refPrice": signal.ref_price,
        "score": signal.score,
        "confidence": signal.confidence,
        "reason": signal.reason,
        "ts": signal.ts.isoformat(timespec="seconds"),
        "marketDate": signal.market_date.isoformat(),
        "metadata": signal.metadata,
    }
