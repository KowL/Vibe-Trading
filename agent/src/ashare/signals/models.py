"""Data models for the signal delivery subsystem.

``NormalizedSignal`` is the broker-agnostic envelope every strategy runner
emits. It is *not* the same as ``src.ashare.strategies.market_models.MatchedSymbol``
because the delivery subsystem needs its own concerns: a dedup key, a delivery
timestamp, and an audit-stable string ``reason``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class NormalizedSignal:
    """One signal to be delivered to all configured sinks.

    Attributes:
        strategy_id: Registered strategy id (e.g. ``"my_bollinger"``).
        market_date: Trade date the signal belongs to (used for daily rollup
            files under ``~/.vibe-trading/ashare/signals/<strategy>/<date>.json``).
        ts: Wall-clock time the signal was emitted by the runner. Used by the
            audit log and as the canonical sort key when multiple signals land
            in the same second.
        symbol: Standardized symbol, e.g. ``"600519.SH"`` or ``"000001.SZ"``.
        side: One of ``"buy"`` / ``"sell"`` / ``"hold"`` / ``"watch"``.
            ``hold`` and ``watch`` are still delivered (for state-tracking)
            but typically excluded from webhook filter.
        ref_price: Reference price at signal time. May be stale; downstream
            UIs must label it "non-realtime" per SPEC §6.4 (A-share T+1).
        score: Numeric score if the strategy emits one (e.g. multi-factor
            composite). Optional.
        confidence: ``[0, 1]`` confidence used by webhook filter
            ``min_confidence`` and by downstream sort.
        reason: Short human-readable string (e.g. ``"break_lower"`` /
            ``"top_10"``). Always carried into the audit log.
        metadata: Free-form dict copied through to the audit log and webhook
            payload. Should remain JSON-serializable; values that cannot be
            serialized will fall back to ``repr()`` at the audit boundary.
    """

    strategy_id: str
    market_date: date
    ts: datetime
    symbol: str
    side: str
    ref_price: float
    score: float | None = None
    confidence: float = 0.0
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def dedup_key(self) -> tuple[str, str, str]:
        """Return the dedup key: (strategy_id, symbol, side).

        同一策略同一标的同一方向在 cooldown 窗口内只推一次（SPEC §1.4）。
        """
        return (self.strategy_id, self.symbol, self.side)


@dataclass(frozen=True)
class DeliveryResult:
    """Outcome of one :meth:`SignalDeliveryService.deliver` call.

    Returned to the strategy runner so it can log/observe delivery success
    without coupling to any specific sink. ``delivered_to`` and
    ``failed_to`` are lists of sink *names* (see :class:`SignalSink.name`).

    Attributes:
        delivered_to: Sink names that accepted the signal.
        failed_to: Sink names that raised (the rest still succeeded).
        deduped: True if the signal was suppressed by the dedup window;
            in that case ``delivered_to`` and ``failed_to`` are both empty.
    """

    delivered_to: list[str]
    failed_to: list[str] = field(default_factory=list)
    deduped: bool = False
