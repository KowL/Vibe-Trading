"""Sink implementations that deliver a :class:`NormalizedSignal` to a target.

Sinks ship in the base package:

- :class:`LocalSink` — appends to ``~/.vibe-trading/ashare/signals/<strategy>/<date>.json``.
- :class:`SSESink` — emits an ``ashare_signal_new`` event on the
  ``src.session.events.EventBus`` (``session_id="ashare_broadcast"``).
- :class:`WebhookSink` — POSTs to Bark / Telegram / WeCom / generic
  webhooks based on the ``~/.vibe-trading/ashare/signals.yaml`` config.
"""

from __future__ import annotations

from src.ashare.signals.sinks.base import SignalSink
from src.ashare.signals.sinks.local_sink import LocalSink
from src.ashare.signals.sinks.sse_sink import SSESink
from src.ashare.signals.sinks.webhook_sink import WebhookSink

__all__ = ["LocalSink", "SSESink", "WebhookSink", "SignalSink"]
