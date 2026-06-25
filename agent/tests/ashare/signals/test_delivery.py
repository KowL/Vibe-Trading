"""Unit tests for the SignalDeliveryService + LocalSink + SSESink + WebhookSink."""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.ashare.signals.audit import SignalAuditLog
from src.ashare.signals.dedup import SignalDeduplicator
from src.ashare.signals.delivery import SignalDeliveryService
from src.ashare.signals.models import NormalizedSignal
from src.ashare.signals.sinks.base import SignalSink
from src.ashare.signals.sinks.local_sink import LocalSink
from src.ashare.signals.sinks.sse_sink import SSESink
from src.ashare.signals.sinks.webhook_sink import WebhookSink
from src.ashare.signals.config import WebhookProviderConfig, WebhookFilterConfig


def _sig(symbol: str = "X.SH", side: str = "buy", confidence: float = 0.9) -> NormalizedSignal:
    return NormalizedSignal(
        strategy_id="my_bollinger", market_date=date(2026, 6, 25),
        ts=datetime(2026, 6, 25, 10, 0, 0), symbol=symbol, side=side,
        ref_price=100.0, confidence=confidence, reason="test",
    )


class _CapturingSink(SignalSink):
    def __init__(self, name: str = "cap") -> None:
        self._name = name
        self.received: list[NormalizedSignal] = []

    def name(self) -> str:
        return self._name

    async def send(self, signal: NormalizedSignal) -> None:
        self.received.append(signal)


class _FailingSink(SignalSink):
    def name(self) -> str:
        return "failing"

    async def send(self, signal: NormalizedSignal) -> None:
        raise RuntimeError("boom")


# --------------------------------------------------------------------------- #
# Service                                                                    #
# --------------------------------------------------------------------------- #


def test_service_fan_out_records_outcomes(tmp_path: Path) -> None:
    audit = SignalAuditLog(path=tmp_path / "a.jsonl")
    dedup = SignalDeduplicator(cooldown_seconds=0)
    ok = _CapturingSink("ok")
    fail = _FailingSink()
    svc = SignalDeliveryService(sinks=[ok, fail], dedup=dedup, audit=audit)

    result = asyncio.run(svc.deliver(_sig()))
    assert result.delivered_to == ["ok"]
    assert result.failed_to == ["failing"]
    assert result.deduped is False
    # 1 received + 1 emitted = 2 lines
    lines = (tmp_path / "a.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert "signal_received" in lines[0]
    assert "signal_emitted" in lines[1]


def test_service_dedup_short_circuits(tmp_path: Path) -> None:
    audit = SignalAuditLog(path=tmp_path / "a.jsonl")
    dedup = SignalDeduplicator(cooldown_seconds=600)
    ok = _CapturingSink("ok")
    svc = SignalDeliveryService(sinks=[ok], dedup=dedup, audit=audit)
    r1 = asyncio.run(svc.deliver(_sig()))
    r2 = asyncio.run(svc.deliver(_sig()))
    assert r1.delivered_to == ["ok"]
    assert r2.deduped is True
    assert r2.delivered_to == []
    # The deduped signal was still audit-recorded.
    recs = [json.loads(l) for l in (tmp_path / "a.jsonl").read_text().splitlines()]
    assert any(r["event"] == "signal_deduped" for r in recs)


def test_service_never_raises_on_sink_failure(tmp_path: Path) -> None:
    audit = SignalAuditLog(path=tmp_path / "a.jsonl")
    svc = SignalDeliveryService(
        sinks=[_FailingSink()], dedup=SignalDeduplicator(cooldown_seconds=0), audit=audit,
    )
    # Must not raise
    result = asyncio.run(svc.deliver(_sig()))
    assert result.failed_to == ["failing"]


# --------------------------------------------------------------------------- #
# LocalSink                                                                  #
# --------------------------------------------------------------------------- #


def test_local_sink_writes_envelope(tmp_path: Path) -> None:
    sink = LocalSink(root=tmp_path / "ashare/signals")
    asyncio.run(sink.send(_sig(symbol="600519.SH")))
    f = tmp_path / "ashare/signals/my_bollinger/2026-06-25.json"
    assert f.is_file()
    data = json.loads(f.read_text())
    assert data["strategy_id"] == "my_bollinger"
    assert data["trade_date"] == "2026-06-25"
    assert data["run_type"] in ("intraday", "eod")
    assert len(data["signals"]) == 1


def test_local_sink_intraday_vs_eod_tagging(tmp_path: Path) -> None:
    sink = LocalSink(root=tmp_path / "ashare/signals")
    # 18:00 → eod
    sig_eod = NormalizedSignal(
        strategy_id="x", market_date=date(2026, 6, 25),
        ts=datetime(2026, 6, 25, 18, 0, 0), symbol="X", side="buy", ref_price=1.0,
    )
    asyncio.run(sink.send(sig_eod))
    data = json.loads((tmp_path / "ashare/signals/x/2026-06-25.json").read_text())
    assert data["run_type"] == "eod"


def test_local_sink_appends_to_same_day(tmp_path: Path) -> None:
    sink = LocalSink(root=tmp_path / "ashare/signals")
    asyncio.run(sink.send(_sig(symbol="A")))
    asyncio.run(sink.send(_sig(symbol="B")))
    data = json.loads((tmp_path / "ashare/signals/my_bollinger/2026-06-25.json").read_text())
    assert len(data["signals"]) == 2
    assert data["signals"][0]["symbol"] == "A"
    assert data["signals"][1]["symbol"] == "B"


# --------------------------------------------------------------------------- #
# SSESink                                                                    #
# --------------------------------------------------------------------------- #


def test_sse_sink_no_bus_no_op() -> None:
    sink = SSESink()
    # No exception even if no bus is wired
    asyncio.run(sink.send(_sig()))


def test_sse_sink_emits_event_when_bus_wired() -> None:
    from src.session.events import EventBus
    from src.ashare.live_publisher import get_publisher
    pub = get_publisher()
    bus = EventBus()
    pub.event_bus = bus
    try:
        sink = SSESink()
        asyncio.run(sink.send(_sig(symbol="600519.SH")))
        events = bus.replay("ashare_broadcast", replay_all=True)
        assert len(events) == 1
        assert events[0].event_type == "ashare_signal_new"
        assert events[0].data["symbol"] == "600519.SH"
    finally:
        pub.event_bus = None


# --------------------------------------------------------------------------- #
# WebhookSink                                                                #
# --------------------------------------------------------------------------- #


def test_webhook_filter_rejects() -> None:
    p = WebhookProviderConfig(
        name="x", url="http://example.test/h",
        filter=WebhookFilterConfig(min_confidence=0.7, strategies=["my_bollinger"], sides=["buy"]),
    )
    sink = WebhookSink([p])
    # low conf
    assert not WebhookSink._matches(p, _sig(confidence=0.5))
    # wrong strategy
    sig_other = NormalizedSignal(
        strategy_id="my_multi_factor", market_date=date(2026, 6, 25),
        ts=datetime(2026, 6, 25, 10, 0), symbol="X", side="buy", ref_price=1.0,
    )
    assert not WebhookSink._matches(p, sig_other)
    # side not in filter
    assert not WebhookSink._matches(p, _sig(side="hold"))
    # ok
    assert WebhookSink._matches(p, _sig())


def test_webhook_send_generic_posts_json() -> None:
    p = WebhookProviderConfig(name="generic", url="http://example.test/h")
    sink = WebhookSink([p])
    real_req = httpx.Request("POST", "http://example.test/h")
    real_resp = httpx.Response(200, json={"ok": True}, request=real_req)

    async def go() -> None:
        with patch("httpx.AsyncClient") as MockClient:
            client = MockClient.return_value
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=None)
            client.request = AsyncMock(return_value=real_resp)
            client.get = AsyncMock(return_value=real_resp)
            await sink.send(_sig())
            args, kwargs = client.request.call_args
            assert args[0] == "POST"
            assert args[1] == "http://example.test/h"
            body = kwargs["json"]
            assert body["symbol"] == "X.SH"
            assert body["side"] == "buy"
            assert body["strategy_id"] == "my_bollinger"

    asyncio.run(go())


def test_webhook_swallows_http_errors() -> None:
    """The sink MUST NOT raise when the HTTP call fails."""
    p = WebhookProviderConfig(name="generic", url="http://example.test/h")
    sink = WebhookSink([p])

    async def go() -> None:
        with patch("httpx.AsyncClient") as MockClient:
            client = MockClient.return_value
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=None)
            # No request attached → raise_for_status will throw
            client.request = AsyncMock(return_value=httpx.Response(500))
            client.get = AsyncMock(return_value=httpx.Response(500))
            # No exception escapes
            await sink.send(_sig())

    asyncio.run(go())
