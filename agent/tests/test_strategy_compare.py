"""Unit tests for strategy compare subsystem."""

from __future__ import annotations

import math
import time
import uuid
from datetime import date, timedelta
from typing import Any
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.ashare.api.routes import router
from src.ashare.strategies.compare_backtest import BacktestOutcome, run_backtest
from src.ashare.strategies.compare_engine import run_compare
from src.ashare.strategies.compare_models import (
    LocalSelectParams,
    MultiFactorParams,
    StrategyCompareRequest,
    StrategyCompareShared,
    StrategySpec,
)
from src.ashare.strategies.selector_registry import (
    UnknownSelectorError,
    list_selectors,
    register_selector,
    resolve_selector,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_trending_panel(
    symbols: list[str],
    start: date,
    end: date,
    seed: int = 42,
) -> dict[str, pd.DataFrame]:
    """Create a fake price panel where every stock has an upward trend.

    This ensures selectors return non-empty picks on most rebalance days.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start=start, end=end, freq="B")
    panel: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(symbols):
        n = len(dates)
        # Deterministic upward drift + noise.
        returns = rng.normal(loc=0.001, scale=0.02, size=n)
        close = 10.0 * (1.0 + i * 0.05) * np.cumprod(1 + returns)
        volume = rng.uniform(1e6, 5e6, size=n)
        amount = volume * close * rng.uniform(0.9, 1.1, size=n)
        df = pd.DataFrame(
            {
                "open": close * rng.uniform(0.99, 1.0, size=n),
                "high": close * rng.uniform(1.0, 1.03, size=n),
                "low": close * rng.uniform(0.97, 1.0, size=n),
                "close": close,
                "volume": volume,
                "amount": amount,
            },
            index=dates,
        )
        panel[sym] = df
    return panel


def _make_request(
    strategies: list[StrategySpec] | None = None,
    start: date = date(2024, 1, 1),
    end: date = date(2024, 3, 1),
) -> StrategyCompareRequest:
    default_strategies = [
        StrategySpec(
            name="local",
            selector="local_select",
            params=LocalSelectParams(top_n=5, rebalance_days=5),
        ),
        StrategySpec(
            name="mf",
            selector="multi_factor",
            params=MultiFactorParams(top_n=5, rebalance_days=5),
        ),
    ]
    return StrategyCompareRequest(
        shared=StrategyCompareShared(
            start_date=start,
            end_date=end,
            initial_cash=1_000_000,
            universe="csi300",
            commission_bps=3,
            slippage_bps=5,
        ),
        strategies=default_strategies if strategies is None else strategies,
    )


# --------------------------------------------------------------------------- #
# Registry tests
# --------------------------------------------------------------------------- #


def test_registry_register_resolve() -> None:
    @register_selector(f"fake_{uuid.uuid4().hex[:8]}")
    def fake_selector(*, trade_date: date, top_n: int, params: dict[str, Any]) -> list[Any]:
        return []

    name = fake_selector.__name__
    # register_selector returns the function unchanged; the actual key is the name passed in.
    # Re-register a second fake with a known key to test resolve.
    key = f"fake2_{uuid.uuid4().hex[:8]}"

    @register_selector(key)
    def fake2(*, trade_date: date, top_n: int, params: dict[str, Any]) -> list[Any]:
        return []

    assert resolve_selector(key) is fake2
    with pytest.raises(UnknownSelectorError):
        resolve_selector(f"not_{key}")


def test_registry_duplicate_raises() -> None:
    key = f"dup_{uuid.uuid4().hex[:8]}"

    @register_selector(key)
    def first(*, trade_date: date, top_n: int, params: dict[str, Any]) -> list[Any]:
        return []

    with pytest.raises(ValueError, match="already registered"):
        @register_selector(key)
        def second(*, trade_date: date, top_n: int, params: dict[str, Any]) -> list[Any]:
            return []


# --------------------------------------------------------------------------- #
# Model validation tests
# --------------------------------------------------------------------------- #


def test_compare_models_validator_strategies_length() -> None:
    with pytest.raises(ValueError):
        _make_request(strategies=[])

    single = StrategySpec(
        name="only",
        selector="local_select",
        params=LocalSelectParams(),
    )
    with pytest.raises(ValueError):
        StrategyCompareRequest(
            shared=_make_request().shared,
            strategies=[single],
        )

    too_many = [
        StrategySpec(
            name=f"s{i}",
            selector="local_select",
            params=LocalSelectParams(),
        )
        for i in range(5)
    ]
    with pytest.raises(ValueError):
        StrategyCompareRequest(
            shared=_make_request().shared,
            strategies=too_many,
        )


def test_compare_models_validator_unique_names() -> None:
    strategies = [
        StrategySpec(name="A", selector="local_select", params=LocalSelectParams()),
        StrategySpec(name="A", selector="multi_factor", params=MultiFactorParams()),
    ]
    with pytest.raises(ValueError, match="unique"):
        StrategyCompareRequest(shared=_make_request().shared, strategies=strategies)


def test_compare_models_validator_date_range() -> None:
    shared = _make_request().shared
    bad_shared = shared.model_copy(update={"end_date": shared.start_date})
    with pytest.raises(ValueError):
        StrategyCompareRequest(shared=bad_shared, strategies=_make_request().strategies)

    far_end = shared.start_date + timedelta(days=365 * 5 + 1)
    bad_shared2 = shared.model_copy(update={"end_date": far_end})
    with pytest.raises(ValueError):
        StrategyCompareRequest(shared=bad_shared2, strategies=_make_request().strategies)


def test_compare_models_validator_selector_params_match() -> None:
    # multi_factor selector with local_select params body should fail.
    with pytest.raises(ValueError):
        StrategySpec(
            name="mismatch",
            selector="multi_factor",
            params=LocalSelectParams(),  # type: ignore[arg-type]
        )


# --------------------------------------------------------------------------- #
# Backtest runner tests
# --------------------------------------------------------------------------- #


def test_run_backtest_local_select_smoke() -> None:
    panel = _make_trending_panel(["000001.SZ", "000002.SZ", "000333.SZ"], date(2024, 1, 1), date(2024, 2, 1))
    shared = StrategyCompareShared(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 2, 1),
        initial_cash=1_000_000,
        universe="csi300",
        commission_bps=3,
        slippage_bps=5,
    )
    spec = StrategySpec(
        name="local",
        selector="local_select",
        params=LocalSelectParams(top_n=5, rebalance_days=5),
    )
    rebars = sorted({d.date() for d in pd.date_range(date(2024, 1, 1), date(2024, 2, 1), freq="B")})
    outcome = run_backtest(
        spec=spec,
        shared=shared,
        selector_fn=resolve_selector("local_select"),
        panel=panel,
        rebalance_dates=rebars,
    )
    assert outcome.equity_curve
    assert all(math.isfinite(v) for v in outcome.metrics.values())


def test_run_backtest_multi_factor_smoke() -> None:
    panel = _make_trending_panel(["000001.SZ", "000002.SZ", "000333.SZ"], date(2024, 1, 1), date(2024, 2, 1))
    shared = StrategyCompareShared(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 2, 1),
        initial_cash=1_000_000,
        universe="csi300",
        commission_bps=3,
        slippage_bps=5,
    )
    spec = StrategySpec(
        name="mf",
        selector="multi_factor",
        params=MultiFactorParams(top_n=5, rebalance_days=5),
    )
    rebars = sorted({d.date() for d in pd.date_range(date(2024, 1, 1), date(2024, 2, 1), freq="B")})
    outcome = run_backtest(
        spec=spec,
        shared=shared,
        selector_fn=resolve_selector("multi_factor"),
        panel=panel,
        rebalance_dates=rebars,
    )
    assert outcome.equity_curve
    assert all(math.isfinite(v) for v in outcome.metrics.values())


def test_run_backtest_strict_json_for_nonfinite() -> None:
    """Engine must coerce any non-finite metric into 0.0."""
    outcome = BacktestOutcome(
        name="x",
        selector="local_select",
        equity_curve=[],
        trades=[],
        metrics={"k": float("nan")},
    )
    # _compute_metrics regenerates finite metrics, so construct an empty curve
    # and verify the returned metrics are all finite.
    from src.ashare.strategies.compare_backtest import _compute_metrics

    metrics = _compute_metrics(
        equity_curve=[],
        trades=[],
        initial_cash=1_000_000,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
    )
    assert all(math.isfinite(v) for v in metrics.values())


# --------------------------------------------------------------------------- #
# Compare engine tests
# --------------------------------------------------------------------------- #


def test_run_compare_aligns_intersection() -> None:
    panel = _make_trending_panel(["000001.SZ", "000002.SZ", "000333.SZ"], date(2024, 1, 1), date(2024, 2, 1))
    req = _make_request(
        start=date(2024, 1, 1),
        end=date(2024, 2, 1),
    )
    with patch("src.ashare.strategies.compare_engine.load_panel_cached", return_value=panel):
        resp = run_compare(req)
    assert len(resp.curves) == 2
    dates_a = [p.date for p in resp.curves[0].points]
    dates_b = [p.date for p in resp.curves[1].points]
    assert dates_a == dates_b
    assert resp.alignment.per_strategy_dropped["local"] == 0
    assert resp.alignment.per_strategy_dropped["mf"] == 0


def test_run_compare_metrics_order_matches_request() -> None:
    panel = _make_trending_panel(["000001.SZ", "000002.SZ", "000333.SZ"], date(2024, 1, 1), date(2024, 2, 1))
    strategies = [
        StrategySpec(name="s1", selector="local_select", params=LocalSelectParams()),
        StrategySpec(name="s2", selector="multi_factor", params=MultiFactorParams()),
        StrategySpec(name="s3", selector="local_select", params=LocalSelectParams(top_n=10)),
    ]
    req = _make_request(strategies=strategies, start=date(2024, 1, 1), end=date(2024, 2, 1))
    with patch("src.ashare.strategies.compare_engine.load_panel_cached", return_value=panel):
        resp = run_compare(req)
    assert [m.name for m in resp.metrics] == ["s1", "s2", "s3"]


def test_run_compare_threads_finish() -> None:
    """Four specs with a 0.3s sleep selector should finish in < 1.5s wall time."""
    key = "local_select"  # use a known key to avoid Pydantic Literal restrictions
    original = resolve_selector(key)

    def sleepy(*, trade_date: date, top_n: int, params: dict[str, Any]) -> list[Any]:
        time.sleep(0.3)
        return []

    # Patch the registry entry for this test.
    from src.ashare.strategies import selector_registry as reg
    reg._REGISTRY[key] = sleepy

    panel = _make_trending_panel(["000001.SZ"], date(2024, 1, 1), date(2024, 1, 15))
    strategies = [
        StrategySpec(
            name=f"x{i}",
            selector="local_select",
            params=LocalSelectParams(),
        )
        for i in range(4)
    ]
    req = _make_request(strategies=strategies, start=date(2024, 1, 1), end=date(2024, 1, 15))
    try:
        with patch("src.ashare.strategies.compare_engine.load_panel_cached", return_value=panel):
            t0 = time.monotonic()
            resp = run_compare(req)
            elapsed = time.monotonic() - t0
    finally:
        reg._REGISTRY[key] = original
    assert elapsed < 1.5
    assert len(resp.metrics) == 4


def test_load_panel_cached_shared_within_call() -> None:
    panel = _make_trending_panel(["000001.SZ"], date(2024, 1, 1), date(2024, 1, 15))
    strategies = [
        StrategySpec(name=f"x{i}", selector="local_select", params=LocalSelectParams())
        for i in range(4)
    ]
    req = _make_request(strategies=strategies, start=date(2024, 1, 1), end=date(2024, 1, 15))
    with patch(
        "src.ashare.strategies.compare_engine.load_panel_cached",
        return_value=panel,
    ) as mock_load:
        run_compare(req)
    mock_load.assert_called_once()


# --------------------------------------------------------------------------- #
# Route tests
# --------------------------------------------------------------------------- #


@pytest.fixture
def client() -> TestClient:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_route_strategy_compare_422_validation(client: TestClient) -> None:
    body = {
        "shared": {
            "start_date": "2024-01-01",
            "end_date": "2024-01-01",
            "initial_cash": 1_000_000,
            "universe": "csi300",
            "commission_bps": 3,
            "slippage_bps": 5,
        },
        "strategies": [
            {"name": "A", "selector": "local_select", "params": {"top_n": 5}},
        ],
    }
    resp = client.post("/ashare/strategy/compare", json=body)
    assert resp.status_code == 422


def test_route_strategy_compare_500_spec_failed(client: TestClient) -> None:
    panel = _make_trending_panel(["000001.SZ"], date(2024, 1, 1), date(2024, 1, 15))
    body = {
        "shared": {
            "start_date": "2024-01-01",
            "end_date": "2024-01-15",
            "initial_cash": 1_000_000,
            "universe": "csi300",
            "commission_bps": 3,
            "slippage_bps": 5,
        },
        "strategies": [
            {"name": "A", "selector": "local_select", "params": {"top_n": 5}},
            {"name": "B", "selector": "local_select", "params": {"top_n": 5}},
        ],
    }

    def boom(*, trade_date: date, top_n: int, params: dict[str, Any]) -> list[Any]:
        raise RuntimeError("intentional")

    from src.ashare.strategies import selector_registry as reg
    original = reg._REGISTRY["local_select"]
    reg._REGISTRY["local_select"] = boom
    try:
        with patch("src.ashare.strategies.compare_engine.load_panel_cached", return_value=panel):
            resp = client.post("/ashare/strategy/compare", json=body)
    finally:
        reg._REGISTRY["local_select"] = original
    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "spec_failed"


def test_route_strategy_compare_500_unknown_selector(client: TestClient) -> None:
    panel = _make_trending_panel(["000001.SZ"], date(2024, 1, 1), date(2024, 1, 15))
    body = {
        "shared": {
            "start_date": "2024-01-01",
            "end_date": "2024-01-15",
            "initial_cash": 1_000_000,
            "universe": "csi300",
            "commission_bps": 3,
            "slippage_bps": 5,
        },
        "strategies": [
            {"name": "A", "selector": "local_select", "params": {"top_n": 5}},
            {"name": "B", "selector": "local_select", "params": {"top_n": 5}},
        ],
    }

    from src.ashare.strategies import selector_registry as reg
    original = reg._REGISTRY.pop("local_select")
    try:
        with patch("src.ashare.strategies.compare_engine.load_panel_cached", return_value=panel):
            resp = client.post("/ashare/strategy/compare", json=body)
    finally:
        reg._REGISTRY["local_select"] = original
    assert resp.status_code == 500
    data = resp.json()["detail"]
    assert data["error"] == "unknown_selector"
    assert "multi_factor" in data["available"]


def test_route_strategy_compare_200_happy_path(client: TestClient) -> None:
    panel = _make_trending_panel(["000001.SZ", "000002.SZ", "000333.SZ"], date(2024, 1, 1), date(2024, 2, 1))
    body = {
        "shared": {
            "start_date": "2024-01-01",
            "end_date": "2024-02-01",
            "initial_cash": 1_000_000,
            "universe": "csi300",
            "commission_bps": 3,
            "slippage_bps": 5,
        },
        "strategies": [
            {"name": "local", "selector": "local_select", "params": {"top_n": 5}},
            {"name": "mf", "selector": "multi_factor", "params": {"top_n": 5}},
        ],
    }
    with patch("src.ashare.strategies.compare_engine.load_panel_cached", return_value=panel):
        resp = client.post("/ashare/strategy/compare", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["metrics"]) == 2
    assert len(data["curves"]) == 2
    for m in data["metrics"]:
        assert math.isfinite(m["total_return_pct"])
        assert math.isfinite(m["sharpe"])
