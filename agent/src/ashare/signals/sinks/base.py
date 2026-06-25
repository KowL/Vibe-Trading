"""Abstract base class for signal delivery sinks."""

from __future__ import annotations

import abc

from src.ashare.signals.models import NormalizedSignal


class SignalSink(abc.ABC):
    """A delivery target for a :class:`NormalizedSignal`.

    Implementations must be safe to call concurrently from the same
    :class:`SignalDeliveryService` (the service fans out with
    :func:`asyncio.gather` + ``return_exceptions=True``). They must also
    not raise on transient I/O errors — instead, swallow the error and
    log; the service records the failure in the audit log under
    ``failed_to`` and the *other* sinks still receive the signal.
    """

    @abc.abstractmethod
    def name(self) -> str:
        """Return a stable identifier (e.g. ``"local"``, ``"sse"``, ``"bark"``).

        Used in :class:`~src.ashare.signals.models.DeliveryResult` and
        in the audit log. Must be lowercase, short, and unique per process.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def send(self, signal: NormalizedSignal) -> None:
        """Deliver ``signal`` to this sink.

        Contract:
            * MUST NOT raise on expected I/O failures. Log and return.
            * SHOULD be idempotent under retry (the service does not
              retry, but webhook providers may).
            * MUST be safe to call from any task (the service creates
              one task per signal).
        """
        raise NotImplementedError
