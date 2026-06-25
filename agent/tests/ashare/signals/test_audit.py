"""Unit tests for the audit log writer."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from src.ashare.signals.audit import SignalAuditLog
from src.ashare.signals.models import NormalizedSignal


def _sig(symbol: str = "X.SH", side: str = "buy") -> NormalizedSignal:
    return NormalizedSignal(
        strategy_id="my_bollinger", market_date=date(2026, 6, 25),
        ts=datetime(2026, 6, 25, 10, 0, 0), symbol=symbol, side=side,
        ref_price=100.0, confidence=0.9, reason="break_lower",
    )


def test_received_event(tmp_path: Path) -> None:
    log = SignalAuditLog(path=tmp_path / "a.jsonl")
    log.write_received(_sig())
    lines = (tmp_path / "a.jsonl").read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["event"] == "signal_received"
    assert rec["strategy_id"] == "my_bollinger"
    assert rec["symbol"] == "X.SH"
    assert rec["side"] == "buy"
    assert rec["ref_price"] == 100.0
    assert rec["reason"] == "break_lower"


def test_deduped_event_carries_reason(tmp_path: Path) -> None:
    log = SignalAuditLog(path=tmp_path / "a.jsonl")
    log.write_deduped(_sig(), reason="cooldown")
    rec = json.loads((tmp_path / "a.jsonl").read_text().strip())
    assert rec["event"] == "signal_deduped"
    assert rec["reason"] == "cooldown"


def test_emitted_event_carries_delivery_lists(tmp_path: Path) -> None:
    log = SignalAuditLog(path=tmp_path / "a.jsonl")
    log.write_emitted(_sig(), delivered_to=["local", "sse"], failed_to=["webhook"])
    rec = json.loads((tmp_path / "a.jsonl").read_text().strip())
    assert rec["event"] == "signal_emitted"
    assert rec["delivered_to"] == ["local", "sse"]
    assert rec["failed_to"] == ["webhook"]


def test_audit_write_failure_does_not_raise(tmp_path: Path, monkeypatch) -> None:
    """If the path is unwritable, the log silently drops the event.

    The audit failure MUST NOT block signal delivery — the service
    records ``failed_to`` separately for sink-level failures.
    """
    log = SignalAuditLog(path=tmp_path / "a.jsonl")
    # Simulate OS error by patching the private writer.
    def boom(_line: str) -> None:
        raise OSError("disk full")
    monkeypatch.setattr(log, "_append_line", boom)
    # Must not raise
    log.write_received(_sig())
    log.write_deduped(_sig(), reason="x")
    log.write_emitted(_sig(), delivered_to=[], failed_to=[])


def test_metadata_serialised_fallback(tmp_path: Path) -> None:
    """A non-JSON-serialisable metadata value falls back to repr().

    The audit record keeps ``reason`` as a top-level field; the rest of
    the signal's metadata is intentionally NOT flattened into the
    audit row (it would dominate the log when a strategy carries
    per-bar context). This test simply asserts that a write with a
    weird metadata value does not raise, which is the contract.
    """
    log = SignalAuditLog(path=tmp_path / "a.jsonl")
    class Weird:
        def __repr__(self) -> str:
            return "<Weird>"
    sig = NormalizedSignal(
        strategy_id="x", market_date=date(2026, 6, 25),
        ts=datetime(2026, 6, 25, 10, 0, 0), symbol="X", side="buy",
        ref_price=1.0, metadata={"obj": Weird()},
    )
    # Must not raise even though metadata is non-serialisable
    log.write_received(sig)
    log.write_emitted(sig, delivered_to=["sse"], failed_to=[])
    lines = (tmp_path / "a.jsonl").read_text().splitlines()
    assert len(lines) == 2
