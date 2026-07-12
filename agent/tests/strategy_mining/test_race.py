"""Tests for the strategy horse race."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.strategy_mining.race import StrategyRace
from tests.strategy_mining.test_miner import _FakeAlpha, _FakeRegistry, _make_panel


def test_strategy_race_picks_winner() -> None:
    panel = _make_panel(n_days=260 * 3, n_stocks=5, seed=42)
    alphas = [_FakeAlpha("alpha_predictive", "momentum")]
    registry = _FakeRegistry(alphas, panel)

    candidates = [
        {"name": "small", "top_n": 2, "use_market_filter": False},
        {"name": "large", "top_n": 4, "use_market_filter": False},
    ]

    race = StrategyRace(
        universe="csi300",
        period="2020-2022",
        candidates=candidates,
        race_window="6M",
        metric="sharpe",
        min_history_months=12,
        panel=panel,
        registry=registry,
    )
    result = race.run()

    assert result.best_name in ("small", "large")
    assert result.best_params is not None
    assert result.best_score is not None
    assert len(result.scores) == 2
    assert "alpha_predictive" in result.final_result.config.selected_alphas


def test_strategy_race_needs_enough_history() -> None:
    panel = _make_panel(n_days=100, n_stocks=3)
    race = StrategyRace(
        universe="csi300",
        period="2020-2020",
        min_history_months=12,
        panel=panel,
    )
    with pytest.raises(ValueError, match="need at least"):
        race.run()


def test_parse_window() -> None:
    assert StrategyRace._parse_window("6M") == {"months": 6}
    assert StrategyRace._parse_window("90D") == {"days": 90}
    with pytest.raises(ValueError):
        StrategyRace._parse_window("1Y")
