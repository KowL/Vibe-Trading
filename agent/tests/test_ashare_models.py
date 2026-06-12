"""Tests for A-share models and storage."""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest

from src.ashare.models.limit_up import LimitUpDaily
from src.ashare.models.portfolio import Portfolio, Trade, TradeSide
from src.ashare.storage.limit_up_store import LimitUpStore
from src.ashare.storage.portfolio_store import PortfolioStore


def test_limit_up_sealed_when_close_equals_limit() -> None:
    rec = LimitUpDaily(
        trade_date=date(2025, 1, 2),
        symbol="000001.SZ",
        name="平安银行",
        limit_up_price=10.0,
        close_price=10.0,
        seal_amount=50_000_000.0,
    )
    assert rec.is_sealed is True
    assert rec.is_opened is False


def test_limit_up_opened_when_open_count_positive() -> None:
    rec = LimitUpDaily(
        trade_date=date(2025, 1, 2),
        symbol="000001.SZ",
        name="平安银行",
        limit_up_price=10.0,
        close_price=10.0,
        open_count=2,
    )
    assert rec.is_opened is True


def test_limit_up_round_trip() -> None:
    rec = LimitUpDaily(
        trade_date=date(2025, 1, 2),
        symbol="000001.SZ",
        name="平安银行",
        limit_up_count=3,
        first_time=time(9, 35, 0),
        last_time=time(14, 55, 0),
    )
    data = rec.to_dict()
    restored = LimitUpDaily.from_dict(data)
    assert restored.symbol == rec.symbol
    assert restored.limit_up_count == 3
    assert restored.first_time == time(9, 35, 0)


def test_trade_amount_auto_computed() -> None:
    t = Trade(
        trade_id="tr_1",
        portfolio_id="pf_1",
        symbol="000001.SZ",
        side=TradeSide.BUY,
        quantity=1000,
        price=10.5,
    )
    assert t.amount == 10_500.0


def test_trade_close_computes_pnl() -> None:
    t = Trade(
        trade_id="tr_1",
        portfolio_id="pf_1",
        symbol="000001.SZ",
        side=TradeSide.BUY,
        quantity=1000,
        price=10.0,
        fee=10.0,
    )
    t.close(11.0, close_fee=10.0)
    assert t.status.value == "closed"
    assert t.pnl == 980.0


def test_portfolio_total_value() -> None:
    pf = Portfolio(portfolio_id="pf_1", initial_cash=100_000.0, cash=80_000.0, market_value=25_000.0)
    assert pf.total_value == 105_000.0


def test_limit_up_store_save_and_load(tmp_path) -> None:
    store = LimitUpStore(root=tmp_path / "limit_up")
    recs = [
        LimitUpDaily(trade_date=date(2025, 1, 2), symbol="000001.SZ", name="A", limit_up_count=1),
        LimitUpDaily(trade_date=date(2025, 1, 2), symbol="600000.SH", name="B", limit_up_count=2),
    ]
    store.save(recs)
    loaded = store.load_day(date(2025, 1, 2))
    assert len(loaded) == 2
    assert loaded["000001.SZ"].name == "A"
    assert loaded["600000.SH"].limit_up_count == 2


def test_portfolio_store_round_trip(tmp_path) -> None:
    store = PortfolioStore(root=tmp_path / "portfolios")
    pf = Portfolio(portfolio_id="pf_test", name="测试", initial_cash=100_000.0, cash=100_000.0)
    store.save_portfolio(pf)
    loaded = store.load_portfolio("pf_test")
    assert loaded.name == "测试"
    assert loaded.initial_cash == 100_000.0


def test_portfolio_store_trades_append(tmp_path) -> None:
    store = PortfolioStore(root=tmp_path / "portfolios")
    t1 = Trade(trade_id="tr_1", portfolio_id="pf_test", symbol="000001.SZ", side=TradeSide.BUY, quantity=100, price=10.0)
    t2 = Trade(trade_id="tr_2", portfolio_id="pf_test", symbol="000002.SZ", side=TradeSide.BUY, quantity=200, price=20.0)
    store.save_trades("pf_test", [t1])
    store.append_trade("pf_test", t2)
    loaded = store.load_trades("pf_test")
    assert len(loaded) == 2
    assert loaded[1].symbol == "000002.SZ"


def test_limit_up_store_honors_custom_root(tmp_path, monkeypatch) -> None:
    """Regression: LimitUpStore.save / load_day / get / load_range / latest_trade_date
    must all resolve paths under self.root, not the module-level default.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))  # ensure default root is elsewhere

    custom_root = tmp_path / "ashare" / "limit_up"
    store = LimitUpStore(root=custom_root)

    rec = LimitUpDaily(
        trade_date=date(2025, 6, 10),
        symbol="000001.SZ",
        name="平安银行",
        limit_up_count=1,
    )
    store.save([rec])

    # File should land under the custom root, not ~/.vibe-trading/...
    assert (custom_root / "2025" / "20250610.jsonl").exists()
    assert not (Path.home() / ".vibe-trading" / "ashare" / "limit_up" / "2025" / "20250610.jsonl").exists()

    # Round-trip through every accessor
    loaded = store.load_day(date(2025, 6, 10))
    assert loaded["000001.SZ"].name == "平安银行"

    assert store.get(date(2025, 6, 10), "000001.SZ") is not None

    ranged = store.load_range(date(2025, 6, 1), date(2025, 6, 30))
    assert date(2025, 6, 10) in ranged

    assert store.latest_trade_date() == date(2025, 6, 10)
