"""Tests for the A-share strategy market.

These tests cover the registry, models, store, engine and runners without
requiring a live adshare backend.  A small number of integration-style tests
skip automatically when no local parquet data is available.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.ashare.strategies import strategy_registry
from src.ashare.strategies.market_engine import StrategyMarketEngine, set_market_engine
from src.ashare.strategies.market_models import (
    MatchedSymbol,
    StrategyCategory,
    StrategyDefinition,
    StrategyMetrics,
    StrategyParam,
    StrategyRunRequest,
    StrategySnapshot,
)
from src.ashare.strategies.market_store import StrategyMarketStore


# --------------------------------------------------------------------------- #
# Registry & models
# --------------------------------------------------------------------------- #


def test_registry_import_populated() -> None:
    """Importing the runner module registers the catalogue strategies."""
    # market_engine imports market_runner as a side-effect
    from src.ashare.strategies.market_engine import get_market_engine

    get_market_engine()
    ids = strategy_registry.list_strategy_ids()
    assert "local_selector" in ids
    assert "trend_timing" in ids
    assert "bollinger_band" in ids
    assert "adaptive_personality" in ids
    assert "stock_profile" in ids


def test_registry_get_definition() -> None:
    from src.ashare.strategies.market_engine import get_market_engine

    get_market_engine()
    definition = strategy_registry.get_definition("local_selector")
    assert definition.category == StrategyCategory.SELECTOR
    assert any(p.id == "top_n" for p in definition.params)


def test_registry_unknown_raises() -> None:
    with pytest.raises(KeyError):
        strategy_registry.get_definition("not_a_strategy")


def test_strategy_definition_model() -> None:
    definition = StrategyDefinition(
        id="demo",
        name="Demo",
        description="A demo strategy.",
        category=StrategyCategory.SELECTOR,
        params=[
            StrategyParam(id="n", name="N", type="int", default=10, min=1, max=100)
        ],
    )
    data = definition.model_dump()
    assert data["id"] == "demo"
    assert data["params"][0]["default"] == 10


def test_snapshot_serialization() -> None:
    snapshot = StrategySnapshot(
        strategy_id="demo",
        run_at=datetime(2025, 1, 1, 10, 0, 0),
        status="success",
        market_date=date(2025, 1, 1),
        matched=[
            MatchedSymbol(symbol="000001.SZ", signal="buy", score=0.8, confidence=0.9, rank=1)
        ],
        metrics=StrategyMetrics(total_return_pct=12.5, sharpe_ratio=1.2),
    )
    data = snapshot.model_dump()
    assert data["strategy_id"] == "demo"
    assert data["matched"][0]["symbol"] == "000001.SZ"
    assert data["metrics"]["total_return_pct"] == 12.5


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #


def test_store_update_and_get() -> None:
    store = StrategyMarketStore()
    snap = StrategySnapshot(strategy_id="s1", run_at=datetime.now(), status="success", matched=[])
    store.update(snap)
    assert store.get("s1") is snap


def test_store_get_all_returns_copy() -> None:
    store = StrategyMarketStore()
    store.update(StrategySnapshot(strategy_id="s1", run_at=datetime.now(), status="success", matched=[]))
    all_snapshots = store.get_all()
    assert "s1" in all_snapshots
    all_snapshots.pop("s1")
    assert store.get("s1") is not None


def test_store_state_serialization() -> None:
    store = StrategyMarketStore()
    store.update(
        StrategySnapshot(
            strategy_id="s1",
            run_at=datetime.now(),
            status="success",
            market_date=date(2025, 1, 1),
            matched=[],
        )
    )
    definitions = [
        StrategyDefinition(id="s1", name="S1", description="", category=StrategyCategory.SELECTOR)
    ]
    state = store.to_state(definitions)
    assert state["snapshots"]["s1"]["strategy_id"] == "s1"
    assert state["strategies"][0]["id"] == "s1"


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_engine_refresh_uses_runner_and_publishes(monkeypatch: Any) -> None:
    engine = StrategyMarketEngine()

    called = {"request": None}

    def fake_runner(request: StrategyRunRequest) -> StrategySnapshot:
        called["request"] = request
        return StrategySnapshot(
            strategy_id=request.strategy_id,
            run_at=datetime.now(),
            status="success",
            market_date=date(2025, 6, 20),
            matched=[MatchedSymbol(symbol="000001.SZ", signal="buy")],
        )

    monkeypatch.setitem(strategy_registry._REGISTRY, "fake_s", (
        StrategyDefinition(id="fake_s", name="Fake", description="", category=StrategyCategory.SELECTOR),
        fake_runner,
    ))

    publisher = MagicMock()
    engine.set_publisher(publisher)

    snap = await engine.refresh("fake_s", market_date=date(2025, 6, 20))

    assert snap.status == "success"
    assert snap.strategy_id == "fake_s"
    assert called["request"] is not None
    assert called["request"].market_date == date(2025, 6, 20)
    publisher.publish_strategy_market.assert_called_once()


@pytest.mark.asyncio
async def test_engine_refresh_all_runs_every_market_strategy(monkeypatch: Any) -> None:
    engine = StrategyMarketEngine(max_concurrent=5)

    def fake_runner(request: StrategyRunRequest) -> StrategySnapshot:
        return StrategySnapshot(
            strategy_id=request.strategy_id,
            run_at=datetime.now(),
            status="success",
            matched=[],
        )

    monkeypatch.setitem(strategy_registry._REGISTRY, "alpha", (
        StrategyDefinition(id="alpha", name="Alpha", description="", category=StrategyCategory.SELECTOR),
        fake_runner,
    ))
    monkeypatch.setitem(strategy_registry._REGISTRY, "beta", (
        StrategyDefinition(id="beta", name="Beta", description="", category=StrategyCategory.TIMING),
        fake_runner,
    ))
    monkeypatch.setattr(strategy_registry, "list_market_strategy_ids", lambda: ["alpha", "beta"])

    results = await engine.refresh_all()
    assert set(results.keys()) == {"alpha", "beta"}
    assert all(s.status == "success" for s in results.values())


def test_registry_hidden_strategies_still_registered() -> None:
    """Signal-delivery strategies remain registered but are not market-visible."""
    from src.ashare.strategies.market_engine import get_market_engine

    get_market_engine()
    assert "my_multi_factor" in strategy_registry.list_strategy_ids()
    assert "my_bollinger" in strategy_registry.list_strategy_ids()
    market_ids = strategy_registry.list_market_strategy_ids()
    assert "my_multi_factor" not in market_ids
    assert "my_bollinger" not in market_ids


def test_engine_catalogue_excludes_hidden_strategies() -> None:
    from src.ashare.strategies.market_engine import get_market_engine

    engine = get_market_engine()
    ids = {d.id for d in engine.catalogue()}
    assert "my_multi_factor" not in ids
    assert "my_bollinger" not in ids
    assert "stock_profile" not in ids


@pytest.mark.asyncio
async def test_engine_running_snapshot_before_completion(monkeypatch: Any) -> None:
    import time

    engine = StrategyMarketEngine()

    def slow_fake_runner(request: StrategyRunRequest) -> StrategySnapshot:
        time.sleep(0.3)
        return StrategySnapshot(
            strategy_id=request.strategy_id,
            run_at=datetime.now(),
            status="success",
            matched=[],
        )

    monkeypatch.setitem(strategy_registry._REGISTRY, "slow", (
        StrategyDefinition(id="slow", name="Slow", description="", category=StrategyCategory.SELECTOR),
        slow_fake_runner,
    ))
    monkeypatch.setattr(strategy_registry, "list_strategy_ids", lambda: ["slow"])

    task = asyncio.create_task(engine.refresh("slow"))
    # Give the running marker a chance to be written
    await asyncio.sleep(0.05)
    running_snap = engine.get_snapshot("slow")
    assert running_snap is not None
    assert running_snap.status == "running"

    await task
    final_snap = engine.get_snapshot("slow")
    assert final_snap.status == "success"


@pytest.mark.asyncio
async def test_engine_refresh_captures_runner_exception(monkeypatch: Any) -> None:
    engine = StrategyMarketEngine()

    def failing_runner(request: StrategyRunRequest) -> StrategySnapshot:
        raise RuntimeError("boom")

    monkeypatch.setitem(strategy_registry._REGISTRY, "bad", (
        StrategyDefinition(id="bad", name="Bad", description="", category=StrategyCategory.SELECTOR),
        failing_runner,
    ))

    snap = await engine.refresh("bad")
    assert snap.status == "error"
    assert "boom" in (snap.error or "")


# --------------------------------------------------------------------------- #
# Runners (with mocked data)
# --------------------------------------------------------------------------- #


def _make_score(
    symbol: str,
    composite: float = 0.8,
    momentum: float = 5.0,
    volume_ratio: float = 1.5,
    ma5: float = 11.0,
    ma20: float = 10.5,
    ma60: float = 10.0,
) -> Any:
    from src.ashare.strategies.multi_factor import StockScore

    score = StockScore(symbol=symbol, composite_score=composite)
    score.momentum_20d = momentum
    score.volume_ratio = volume_ratio
    score.ma5 = ma5
    score.ma20 = ma20
    score.ma60 = ma60
    return score


def test_selector_runner_returns_top_stocks(monkeypatch: Any) -> None:
    from src.ashare.strategies import market_runner

    scores = [_make_score("000001.SZ"), _make_score("000002.SZ", composite=0.6)]
    monkeypatch.setattr(market_runner, "local_select", lambda **kwargs: scores)

    request = StrategyRunRequest(strategy_id="local_selector", run_backtest=False)
    snapshot = market_runner._run_selector(request)

    assert snapshot.status == "success"
    assert len(snapshot.matched) == 2
    assert snapshot.matched[0].symbol == "000001.SZ"
    assert snapshot.matched[0].signal == "buy"  # score 0.8 + volume_ratio 1.5 触发买入阈值
    assert snapshot.matched[1].symbol == "000002.SZ"
    assert snapshot.matched[1].signal == "watch"  # score 0.6 未达买入阈值


def test_timing_runner_generates_buy_signals(monkeypatch: Any) -> None:
    from src.ashare.strategies import market_runner

    scores = [
        _make_score("000001.SZ", momentum=5.0, volume_ratio=1.5, ma5=11, ma20=10.5, ma60=10),
    ]
    monkeypatch.setattr(market_runner, "trend_select", lambda **kwargs: scores)

    request = StrategyRunRequest(strategy_id="trend_timing", run_backtest=False)
    snapshot = market_runner._run_timing(request)

    assert snapshot.status == "success"
    buy_symbols = [m.symbol for m in snapshot.matched if m.signal == "buy"]
    assert "000001.SZ" in buy_symbols


def test_band_runner_buy_signal_when_below_lower_band(monkeypatch: Any) -> None:
    from src.ashare.strategies import market_runner

    import pandas as pd
    import numpy as np

    scores = [_make_score("000001.SZ", momentum=3.0, volume_ratio=1.5)]
    monkeypatch.setattr(market_runner, "mean_reversion_select", lambda **kwargs: scores)

    dates = pd.date_range("2025-05-01", "2025-06-20")
    # Price descends below the lower Bollinger band on the last day
    prices = np.concatenate([np.linspace(12, 10.5, len(dates) - 1), [8.5]])
    df = pd.DataFrame(
        {
            "open": prices,
            "high": prices * 1.01,
            "low": prices * 0.99,
            "close": prices,
            "volume": [1_000_000] * len(dates),
            "amount": [10_000_000] * len(dates),
        },
        index=dates,
    )

    class FakeLoader:
        def load(self, symbol: str, begin: Any, end: Any) -> Any:
            return df

    monkeypatch.setattr(market_runner, "LocalKlineLoader", lambda data_root: FakeLoader())

    request = StrategyRunRequest(strategy_id="bollinger_band", run_backtest=False)
    snapshot = market_runner._run_band(request)

    assert snapshot.status == "success"
    assert any(m.symbol == "000001.SZ" and m.signal == "buy" for m in snapshot.matched)


def test_profile_runner_requires_symbol() -> None:
    from src.ashare.strategies import market_runner

    request = StrategyRunRequest(strategy_id="stock_profile", params={})
    snapshot = market_runner._run_profile(request)

    assert snapshot.status == "error"
    assert "symbol" in (snapshot.error or "").lower()


def test_profile_runner_returns_profile(monkeypatch: Any) -> None:
    from src.ashare.strategies import market_runner
    from src.ashare.strategies.stock_profile import StockProfile

    profile = StockProfile(symbol="000001.SZ", personality="trending", risk_level="medium")
    monkeypatch.setattr(market_runner.StockProfile, "from_bars", lambda df, symbol: profile)

    import pandas as pd

    df = pd.DataFrame(
        {
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1],
            "amount": [1.0],
        },
        index=pd.to_datetime(["2025-06-20"]),
    )

    class FakeLoader:
        def load(self, symbol: str, begin: Any, end: Any) -> Any:
            return df

    monkeypatch.setattr(market_runner, "LocalKlineLoader", lambda data_root: FakeLoader())

    request = StrategyRunRequest(strategy_id="stock_profile", params={"symbol": "000001.SZ"})
    snapshot = market_runner._run_profile(request)

    assert snapshot.status == "success"
    assert snapshot.matched[0].symbol == "000001.SZ"
    assert snapshot.matched[0].metadata["profile"]["personality"] == "trending"


# --------------------------------------------------------------------------- #
# API routes
# --------------------------------------------------------------------------- #


def test_market_api_returns_state() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.ashare.api import market_routes
    from src.ashare.strategies.market_engine import StrategyMarketEngine

    app = FastAPI()
    app.include_router(market_routes.router)

    engine = StrategyMarketEngine()
    engine.store.update(
        StrategySnapshot(
            strategy_id="local_selector",
            run_at=datetime.now(),
            status="success",
            market_date=date(2025, 6, 20),
            matched=[],
        )
    )
    set_market_engine(engine)

    client = TestClient(app)
    resp = client.get("/strategy-market")
    assert resp.status_code == 200
    data = resp.json()
    assert "snapshots" in data
    assert "local_selector" in data["snapshots"]
