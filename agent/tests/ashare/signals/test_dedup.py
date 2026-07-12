"""Unit tests for the signal dedup component."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from src.ashare.signals.dedup import SignalDeduplicator
from src.ashare.signals.models import NormalizedSignal


def _sig(symbol: str = "X.SH", side: str = "buy") -> NormalizedSignal:
    return NormalizedSignal(
        strategy_id="my_bollinger", market_date=date(2026, 6, 25),
        ts=datetime(2026, 6, 25, 10, 0, 0), symbol=symbol, side=side,
        ref_price=100.0, confidence=0.9, reason="test",
    )


def test_first_emit_returns_true() -> None:
    d = SignalDeduplicator(cooldown_seconds=600)
    assert d.should_emit(_sig()) is True
    assert d.stats()["tracked_keys"] == 1


def test_repeat_within_window_returns_false() -> None:
    d = SignalDeduplicator(cooldown_seconds=600)
    assert d.should_emit(_sig()) is True
    # Same (strategy, symbol, side) again → False
    assert d.should_emit(_sig()) is False
    assert d.stats()["tracked_keys"] == 1


def test_different_side_is_independent() -> None:
    d = SignalDeduplicator(cooldown_seconds=600)
    assert d.should_emit(_sig(side="buy")) is True
    # Same symbol, different side → independent key
    assert d.should_emit(_sig(side="sell")) is True
    assert d.stats()["tracked_keys"] == 2


def test_different_symbol_is_independent() -> None:
    d = SignalDeduplicator(cooldown_seconds=600)
    assert d.should_emit(_sig(symbol="A.SH")) is True
    assert d.should_emit(_sig(symbol="B.SH")) is True
    assert d.stats()["tracked_keys"] == 2


def test_cooldown_expires_via_fake_clock() -> None:
    fake_now = [0.0]
    d = SignalDeduplicator(cooldown_seconds=10, clock=lambda: fake_now[0])
    assert d.should_emit(_sig()) is True
    fake_now[0] = 5.0
    assert d.should_emit(_sig()) is False
    fake_now[0] = 11.0
    assert d.should_emit(_sig()) is True


def test_max_keys_evicts_oldest() -> None:
    d = SignalDeduplicator(cooldown_seconds=999, max_keys=3)
    for i in range(5):
        assert d.should_emit(_sig(symbol=f"S{i}.SH")) is True
    # Only the 3 most recent keys should remain
    assert d.stats()["tracked_keys"] == 3
    # The two oldest were evicted
    assert d.should_emit(_sig(symbol="S0.SH")) is True  # re-admitted
    assert d.should_emit(_sig(symbol="S1.SH")) is True


def test_clear_drops_all() -> None:
    d = SignalDeduplicator(cooldown_seconds=600)
    d.should_emit(_sig(symbol="A"))
    d.should_emit(_sig(symbol="B"))
    assert d.stats()["tracked_keys"] == 2
    d.clear()
    assert d.stats()["tracked_keys"] == 0


def test_zero_cooldown_allows_immediate_repeat() -> None:
    d = SignalDeduplicator(cooldown_seconds=0)
    assert d.should_emit(_sig()) is True
    assert d.should_emit(_sig()) is True


def test_negative_cooldown_rejected() -> None:
    with pytest.raises(ValueError):
        SignalDeduplicator(cooldown_seconds=-1)
