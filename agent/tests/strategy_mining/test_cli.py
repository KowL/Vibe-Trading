"""CLI tests for the strategy mining subcommand."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.strategy_mining.cli_handlers import cmd_strategy_mine, cmd_strategy_race, cmd_strategy_search


@dataclass
class _FakeMineResult:
    config: Any
    report: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=lambda: {"sharpe": 1.2})


@dataclass
class _FakeConfig:
    selected_alphas: list[str] = field(default_factory=lambda: ["a1", "a2"])

    def write(self, path: Path) -> None:
        path.write_text(json.dumps({"selected_alphas": self.selected_alphas}), encoding="utf-8")


def test_cmd_strategy_mine_writes_config_and_report(tmp_path: Path) -> None:
    args = argparse.Namespace(
        universe="csi300",
        period="2020-2020",
        train_years=1,
        top_n=10,
        max_per_theme=2,
        min_ic=0.01,
        min_ic_positive_ratio=0.55,
        min_t_stat=2.0,
        commission=0.0003,
        slippage=0.001,
        no_market_filter=True,
        strict=False,
        n_random_seeds=5,
        alpha_t_threshold=2.0,
        neutralize=False,
        neutralize_fields="sector",
        market_cap_buckets=5,
        replacement_buffer=0.0,
        output_dir=str(tmp_path),
        verbose=False,
    )

    fake_result = _FakeMineResult(
        config=_FakeConfig(selected_alphas=["a1", "a2"]),
        report={"n_rebalances": 12},
        metrics={"sharpe": 1.2},
    )
    fake_miner = MagicMock()
    fake_miner.mine.return_value = fake_result

    hyp_path = tmp_path / "hypotheses.json"
    os.environ["VIBE_TRADING_HYPOTHESES_PATH"] = str(hyp_path)

    with patch("src.strategy_mining.cli_handlers.RollingICMiner", return_value=fake_miner):
        rc = cmd_strategy_mine(args)

    assert rc == 0
    files = list(tmp_path.iterdir())
    assert any("strategy_" in f.name and f.suffix == ".json" for f in files)
    assert any("strategy_report_" in f.name for f in files)

    assert hyp_path.exists()
    data = json.loads(hyp_path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["universe"] == "csi300"
    assert len(data[0]["run_cards"]) == 1


@dataclass
class _FakeSearchResult:
    best_params: dict[str, Any] = field(default_factory=lambda: {"top_n": 30})
    best_score: float = 1.5
    metric: str = "sharpe"
    n_folds: int = 3
    final_result: Any = None

    def write(self, path: Path) -> None:
        path.write_text(json.dumps({"best_score": self.best_score}), encoding="utf-8")


def test_cmd_strategy_search_writes_artifacts(tmp_path: Path) -> None:
    args = argparse.Namespace(
        universe="csi300",
        period="2020-2024",
        n_folds=2,
        metric="sharpe",
        param_grid=None,
        neutralize=False,
        neutralize_fields="sector",
        market_cap_buckets=5,
        replacement_buffer=0.0,
        output_dir=str(tmp_path),
        verbose=False,
    )

    fake_search_result = _FakeSearchResult(
        final_result=_FakeMineResult(
            config=_FakeConfig(selected_alphas=["a1"]),
            report={"n_rebalances": 20},
            metrics={"sharpe": 1.5},
        ),
    )
    fake_search = MagicMock()
    fake_search.fit.return_value = fake_search_result

    hyp_path = tmp_path / "hypotheses.json"
    os.environ["VIBE_TRADING_HYPOTHESES_PATH"] = str(hyp_path)

    with patch("src.strategy_mining.cli_handlers.WalkForwardGridSearch", return_value=fake_search):
        rc = cmd_strategy_search(args)

    assert rc == 0
    files = list(tmp_path.iterdir())
    assert any("strategy_" in f.name and f.suffix == ".json" for f in files)
    assert any("strategy_report_" in f.name for f in files)
    assert any("strategy_search_" in f.name for f in files)

    assert hyp_path.exists()
    data = json.loads(hyp_path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["run_cards"][0]["metrics"]["sharpe"] == 1.5


@dataclass
class _FakeRaceResult:
    best_params: dict[str, Any] = field(default_factory=lambda: {"top_n": 30})
    best_score: float = 1.5
    best_name: str = "winner"
    metric: str = "sharpe"
    race_window: str = "6M"
    final_result: Any = None

    def write(self, path: Path) -> None:
        path.write_text(json.dumps({"best_name": self.best_name}), encoding="utf-8")


def test_cmd_strategy_race_writes_artifacts(tmp_path: Path) -> None:
    args = argparse.Namespace(
        universe="csi300",
        period="2020-2024",
        candidates=None,
        race_window="6M",
        metric="sharpe",
        output_dir=str(tmp_path),
        verbose=False,
    )

    fake_race_result = _FakeRaceResult(
        final_result=_FakeMineResult(
            config=_FakeConfig(selected_alphas=["a1"]),
            report={"n_rebalances": 20},
            metrics={"sharpe": 1.5},
        ),
    )
    fake_race = MagicMock()
    fake_race.run.return_value = fake_race_result

    hyp_path = tmp_path / "hypotheses.json"
    os.environ["VIBE_TRADING_HYPOTHESES_PATH"] = str(hyp_path)

    with patch("src.strategy_mining.cli_handlers.StrategyRace", return_value=fake_race):
        rc = cmd_strategy_race(args)

    assert rc == 0
    files = list(tmp_path.iterdir())
    assert any("strategy_" in f.name and f.suffix == ".json" for f in files)
    assert any("strategy_report_" in f.name for f in files)
    assert any("strategy_race_" in f.name for f in files)

    assert hyp_path.exists()
    data = json.loads(hyp_path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["run_cards"][0]["metrics"]["sharpe"] == 1.5
