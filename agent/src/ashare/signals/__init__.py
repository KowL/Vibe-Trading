"""A-share signal delivery subsystem.

Provides :class:`SignalDeliveryService` for routing strategy-matched signals
to multiple sinks (local JSON, SSE bus, webhooks), with in-process dedup
and append-only audit logging.
"""

from __future__ import annotations

from src.ashare.signals.delivery import (
    SignalDeliveryService,
    get_delivery_service,
    reset_delivery_service,
)
from src.ashare.signals.models import DeliveryResult, NormalizedSignal

__all__ = [
    "DeliveryResult",
    "NormalizedSignal",
    "SignalDeliveryService",
    "get_delivery_service",
    "reset_delivery_service",
]
