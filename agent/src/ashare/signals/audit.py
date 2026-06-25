"""Append-only audit log for the signal delivery subsystem.

Every signal goes through three lifecycle events:

* ``signal_received`` — written before dedup, so a rejected signal is
  still traceable.
* ``signal_deduped`` — written when the dedup window suppresses a signal.
* ``signal_emitted`` — written after the per-sink fan-out, carrying the
  ``delivered_to`` and ``failed_to`` sink-name lists.

Sink-level failures (one of three sinks raised) are *not* logged here;
the service already encodes them in ``signal_emitted.failed_to``. Sink
implementations log their own internal errors with ``logger.warning``.

The file is JSONL at ``~/.vibe-trading/ashare/audit/signals.jsonl``.
Per SPEC §3.3 it is intentionally unrotated: at < 10K events/day the
file is small and grep-friendly.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.ashare.signals.models import NormalizedSignal

logger = logging.getLogger(__name__)


_DEFAULT_AUDIT_SUBDIR = "ashare/audit"
_AUDIT_FILENAME = "signals.jsonl"


def default_audit_path() -> Path:
    """Return the default audit log path, creating parent dirs."""
    path = Path.home() / ".vibe-trading" / _DEFAULT_AUDIT_SUBDIR / _AUDIT_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class SignalAuditLog:
    """Append-only JSONL writer for signal lifecycle events.

    The writer is intentionally minimal: no rotation, no aggregation, no
    async. Every write is fsync'd so a crash mid-run does not lose an
    event (the project's session message log does the same; see PR #147
    referenced in ``AGENT_CONTRIBUTOR_GUIDE.md``).

    Args:
        path: Output file. Defaults to :func:`default_audit_path`.
        clock: Injected ``() -> datetime`` for tests. The default
            returns timezone-aware UTC, matching the rest of the
            project's audit convention.
    """

    def __init__(
        self,
        path: Path | str | None = None,
        clock: Any = None,
    ) -> None:
        self._path = Path(path) if path is not None else default_audit_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------ #
    # public events                                                      #
    # ------------------------------------------------------------------ #

    def write_received(self, signal: NormalizedSignal) -> None:
        self._write_event("signal_received", signal)

    def write_deduped(self, signal: NormalizedSignal, *, reason: str) -> None:
        self._write_event("signal_deduped", signal, extra={"reason": reason})

    def write_emitted(
        self,
        signal: NormalizedSignal,
        *,
        delivered_to: list[str],
        failed_to: list[str],
    ) -> None:
        self._write_event(
            "signal_emitted",
            signal,
            extra={"delivered_to": delivered_to, "failed_to": failed_to},
        )

    # ------------------------------------------------------------------ #
    # generic writer                                                     #
    # ------------------------------------------------------------------ #

    def _write_event(
        self,
        event: str,
        signal: NormalizedSignal,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "ts": self._clock().isoformat(),
            "event": event,
            "strategy_id": signal.strategy_id,
            "symbol": signal.symbol,
            "side": signal.side,
            "ref_price": signal.ref_price,
            "reason": signal.reason,
        }
        if signal.score is not None:
            record["score"] = signal.score
        if signal.confidence:
            record["confidence"] = signal.confidence
        if extra:
            record.update(extra)
        line = json.dumps(record, ensure_ascii=False, default=_safe_default)
        try:
            self._append_line(line)
        except OSError as exc:
            # Audit write failures MUST NOT break signal delivery.
            # Log loudly so the operator notices and can recover the
            # signal from the in-memory ``MatchedSymbol`` list on the
            # strategy snapshot.
            logger.error("audit write failed for %s: %s", event, exc)

    def _append_line(self, line: str) -> None:
        """Write ``line`` + ``\\n`` with fsync. Atomic across crashes.

        Uses a same-directory temp file + ``os.replace`` so a partial
        line can never leave a torn record (the rest of the project
        uses the same idiom; see ``src/live/halt.py:trip_halt``).
        """
        # The actual append is ``open(..., "a")``; we still fsync so a
        # power loss between write and close does not lose the line.
        fd = os.open(self._path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)


def _safe_default(value: Any) -> Any:
    """json.dumps fallback for non-serializable metadata values."""
    # ``date``/``datetime`` are common metadata values from strategies.
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return repr(value)
