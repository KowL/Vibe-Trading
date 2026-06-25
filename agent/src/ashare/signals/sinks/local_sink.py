"""Write :class:`NormalizedSignal` records to per-day JSON files.

Layout (SPEC §3.2)::

    <root>/<strategy_id>/<YYYY-MM-DD>.json
    {
      "schema_version": 1,
      "strategy_id": "my_bollinger",
      "trade_date": "2026-06-24",
      "generated_at": "2026-06-24T14:35:12+08:00",
      "run_type": "intraday" | "eod",
      "signals": [ { ... signal envelope ... }, ... ]
    }

Writes are serialized through an ``asyncio.Lock`` so concurrent
``SignalDeliveryService.deliver`` calls (the service fans out to all
sinks concurrently) do not race on the same per-day file.

The sink is "add-only": the file is rewritten in full on every signal
because N is small per day (tens, not thousands). The trade-off
favours readability and crash-safety over append performance.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any

from src.ashare.signals.models import NormalizedSignal

logger = logging.getLogger(__name__)

_DEFAULT_ROOT = "ashare/signals"
_SCHEMA_VERSION = 1


def default_signals_root() -> Path:
    """Return the default root, creating it if missing.

    Mirrors :func:`src.ashare.signals.audit.default_audit_path` —
    always under ``~/.vibe-trading/ashare/signals`` so the
    audit/snapshot/sink files live next to each other.
    """
    path = Path.home() / ".vibe-trading" / _DEFAULT_ROOT
    path.mkdir(parents=True, exist_ok=True)
    return path


def _infer_run_type(signal: NormalizedSignal) -> str:
    """Tag the day's file with intraday vs EOD based on the timestamp.

    Heuristic: timestamps inside the A-share trading session
    (09:30-11:30, 13:00-15:00 Asia/Shanghai) are ``intraday``;
    anything else is ``eod``. Centralized here so the JSON envelope
    stays consistent across runners.
    """
    from zoneinfo import ZoneInfo

    sh = signal.ts.astimezone(ZoneInfo("Asia/Shanghai"))
    t = sh.time()
    intraday = (
        (time(9, 30) <= t < time(11, 30)) or (time(13, 0) <= t < time(15, 0))
    )
    return "intraday" if intraday else "eod"


class LocalSink:
    """Append signals to ``<root>/<strategy_id>/<date>.json``."""

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = Path(root) if root is not None else default_signals_root()
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def name(self) -> str:
        return "local"

    async def send(self, signal: NormalizedSignal) -> None:
        """Write ``signal`` into the day's file under its strategy dir.

        Holds an asyncio lock so concurrent sends for the same strategy
        do not interleave their full-file rewrite. Errors are caught
        and logged; the service records the failure in the audit log.
        """
        async with self._lock:
            try:
                envelope = self._load_or_init(signal)
                envelope["signals"].append(_signal_to_dict(signal))
                envelope["generated_at"] = signal.ts.isoformat(timespec="seconds")
                self._write_envelope(signal.strategy_id, signal.market_date, envelope)
            except OSError as exc:
                logger.warning(
                    "local sink write failed for %s %s: %s",
                    signal.symbol, signal.side, exc,
                )

    # ------------------------------------------------------------------ #
    # internals                                                           #
    # ------------------------------------------------------------------ #

    def _file_path(self, strategy_id: str, market_date) -> Path:
        return (
            self._root / strategy_id / f"{market_date.isoformat()}.json"
        )

    def _load_or_init(self, signal: NormalizedSignal) -> dict[str, Any]:
        path = self._file_path(signal.strategy_id, signal.market_date)
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                # Corrupt file: rename and start fresh. The user can
                # inspect the renamed file for recovery.
                logger.warning(
                    "local sink: corrupted file %s, renaming: %s",
                    path, exc,
                )
                path.rename(path.with_suffix(f".corrupt.{int(datetime.now().timestamp())}.json"))
        return {
            "schema_version": _SCHEMA_VERSION,
            "strategy_id": signal.strategy_id,
            "trade_date": signal.market_date.isoformat(),
            "generated_at": signal.ts.isoformat(timespec="seconds"),
            "run_type": _infer_run_type(signal),
            "signals": [],
        }

    def _write_envelope(self, strategy_id: str, market_date, envelope: dict[str, Any]) -> None:
        path = self._file_path(strategy_id, market_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: temp + os.replace so a crash mid-write does not
        # leave a half-written envelope. Same idiom as the audit log
        # and the project's halt sentinel.
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)


def _signal_to_dict(signal: NormalizedSignal) -> dict[str, Any]:
    """Project :class:`NormalizedSignal` into the JSON envelope shape.

    The shape matches SPEC §3.2 verbatim so downstream consumers
    (frontend, CLI) do not need to know about the dataclass.
    """
    record: dict[str, Any] = {
        "ts": signal.ts.isoformat(timespec="seconds"),
        "symbol": signal.symbol,
        "side": signal.side,
        "ref_price": signal.ref_price,
        "score": signal.score,
        "confidence": signal.confidence,
        "rank": None,
        "reason": signal.reason,
        "metadata": signal.metadata,
    }
    return record
