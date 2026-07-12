"""Tests for walk-forward grid search."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.factors.registry import Registry
from src.strategy_mining.search import WalkForwardGridSearch, slice_panel
from tests.strategy_mining.test_miner import _FakeAlpha, _FakeRegistry, _make_panel


def test_slice_panel_preserves_shape() -> None:
    panel = _make_panel(n_days=100, n_stocks=3)
    start = panel["close"].index[20]
    end = panel["close"].index[80]
    sliced = slice_panel(panel, start, end)
    assert sliced["close"].shape[1] == panel["close"].shape[1]
    assert sliced["close"].index[0] >= start
    assert sliced["close"].index[-1] <= end


def test_walk_forward_grid_search_picks_best_params() -> None:
    # 5 years of data so n_folds=2 validation folds (2023, 2024) work.
    panel = _make_panel(n_days=260 * 5, n_stocks=5, seed=42)
    alphas = [
        _FakeAlpha("alpha_predictive", "momentum"),
        _FakeAlpha("alpha_noise", "value", noise=True),
    ]
    registry = _FakeRegistry(alphas, panel)

    grid = {
        "top_n": [2, 4],
        "use_market_filter": [False],
        "use_random_control": [True],
        "n_random_seeds": [2],
    }

    search = WalkForwardGridSearch(
        universe="csi300",
        period="2020-2024",
        param_grid=grid,
        n_folds=2,
        metric="sharpe",
        min_train_years=2,
        panel=panel,
        registry=registry,
    )
    result = search.fit()

    assert result.best_params is not None
    assert result.best_score is not None
    assert result.metric == "sharpe"
    assert result.n_folds == 2
    assert len(result.scores) == 2  # two top_n values
    # Final model trained on full period should only contain the predictive alpha.
    assert "alpha_predictive" in result.final_result.config.selected_alphas
    assert "alpha_noise" not in result.final_result.config.selected_alphas


def test_search_with_fewer_years_raises() -> None:
    panel = _make_panel(n_days=100, n_stocks=3)
    search = WalkForwardGridSearch(
        universe="csi300",
        period="2020-2020",
        n_folds=3,
        min_train_years=2,
        panel=panel,
    )
    with pytest.raises(ValueError, match="not enough history"):
        search.fit()


def test_validation_score_metrics() -> None:
    panel = _make_panel(n_days=260 * 3, n_stocks=5, seed=42)
    alphas = [_FakeAlpha("alpha_predictive", "momentum")]
    registry = _FakeRegistry(alphas, panel)

    for metric in ("sharpe", "information_ratio", "annual_return_pct", "calmar"):
        search = WalkForwardGridSearch(
            universe="csi300",
            period="2020-2022",
            param_grid={"top_n": [3], "use_market_filter": [False]},
            n_folds=1,
            metric=metric,
            min_train_years=1,
            panel=panel,
            registry=registry,
        )
        result = search.fit()
        assert math.isfinite(result.best_score)
