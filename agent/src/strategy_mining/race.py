"""Multi-candidate strategy horse race.

Builds several strategy variants (candidates), evaluates each on a recent
out-of-sample window, and returns the best one. This is the ``significantly
better before replacing`` mechanism from the maintenance branch of the design.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.strategy_mining.miner import MineResult, RollingICMiner
from src.tools.alpha_bench_tool import _load_universe_panel

logger = logging.getLogger(__name__)


DEFAULT_CANDIDATES: list[dict[str, Any]] = [
    {"name": "conservative", "top_n": 20, "max_per_theme": 2, "use_market_filter": True},
    {"name": "balanced", "top_n": 30, "max_per_theme": 3, "use_market_filter": True},
    {"name": "aggressive", "top_n": 50, "max_per_theme": 5, "use_market_filter": False},
]


@dataclass
class RaceResult:
    """Output of ``StrategyRace.run()``."""

    best_params: dict[str, Any]
    best_score: float
    best_name: str
    scores: list[dict[str, Any]]
    final_result: MineResult
    metric: str = "sharpe"
    race_window: str = "6M"

    def summary(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "race_window": self.race_window,
            "best_name": self.best_name,
            "best_params": self.best_params,
            "best_score": self.best_score,
            "scores": self.scores,
        }

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.summary(), indent=2, default=str), encoding="utf-8")


class StrategyRace:
    """Run multiple strategy candidates on a recent window and pick the winner.

    Args:
        universe: ``csi300`` | ``sp500`` | ``btc-usdt``.
        period: ``YYYY-YYYY`` or ``YYYY-MM-DD/YYYY-MM-DD``.
        candidates: List of parameter dicts. Each dict must include a ``name``
            key. Defaults to ``DEFAULT_CANDIDATES``.
        race_window: Recent window to evaluate candidates on. Supports
            pandas offsets like ``6M`` (6 months) or ``90D``.
        metric: Metric used to rank candidates. One of ``sharpe``,
            ``information_ratio``, ``annual_return_pct``, ``calmar``.
        min_history_months: Minimum panel history required for a fair race.
        panel: Optional pre-built panel to avoid reloading.
        registry: Optional registry (mostly for tests).
    """

    _METRICS: tuple[str, ...] = ("sharpe", "information_ratio", "annual_return_pct", "calmar")

    def __init__(
        self,
        universe: str = "csi300",
        period: str = "2020-2025",
        candidates: list[dict[str, Any]] | None = None,
        race_window: str = "6M",
        metric: str = "sharpe",
        min_history_months: int = 12,
        panel: dict[str, pd.DataFrame] | None = None,
        registry: Any | None = None,
    ) -> None:
        if metric not in self._METRICS:
            raise ValueError(f"metric {metric!r} not in {self._METRICS}")
        self.universe = universe
        self.period = period
        self.candidates = candidates if candidates is not None else DEFAULT_CANDIDATES
        self.race_window = race_window
        self.metric = metric
        self.min_history_months = min_history_months
        self._provided_panel = panel
        self._registry = registry

        self.panel: dict[str, pd.DataFrame] | None = None

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #

    def run(self) -> RaceResult:
        """Run the horse race and return the winning candidate + final strategy."""
        logger.info("strategy race: universe=%s period=%s", self.universe, self.period)
        self.panel = self._provided_panel or _load_universe_panel(self.universe, self.period)

        close = self.panel["close"]
        if close.empty:
            raise ValueError("panel is empty")

        required_start = close.index.max() - pd.DateOffset(months=self.min_history_months)
        if close.index.min() > required_start:
            raise ValueError(
                f"need at least {self.min_history_months} months of history for a strategy race"
            )

        race_start = close.index.max() - pd.DateOffset(**self._parse_window(self.race_window))
        race_start = max(race_start, close.index.min())

        scores: list[dict[str, Any]] = []
        for candidate in self.candidates:
            name = candidate.pop("name", "unnamed")
            logger.info("strategy race: evaluating candidate %s", name)
            miner = RollingICMiner(
                universe=self.universe,
                period=self.period,
                registry=self._registry,
                panel=self.panel,
                **candidate,
            )
            result = miner.mine()
            score = self._evaluate(result, race_start)
            scores.append(
                {
                    "name": name,
                    "params": candidate,
                    "score": round(score, 4),
                    "metrics": result.metrics,
                }
            )

        if not scores:
            raise RuntimeError("no candidates evaluated")

        best = max(scores, key=lambda x: x["score"])
        best_params = dict(best["params"])
        logger.info("strategy race: winner=%s score=%.4f", best["name"], best["score"])

        final_miner = RollingICMiner(
            universe=self.universe,
            period=self.period,
            registry=self._registry,
            panel=self.panel,
            **best_params,
        )
        final_result = final_miner.mine()

        return RaceResult(
            best_params=best_params,
            best_score=best["score"],
            best_name=best["name"],
            scores=scores,
            final_result=final_result,
            metric=self.metric,
            race_window=self.race_window,
        )

    # --------------------------------------------------------------------- #
    # Helpers
    # --------------------------------------------------------------------- #

    @staticmethod
    def _parse_window(window: str) -> dict[str, int]:
        """Parse ``6M`` / ``90D`` style offsets into kwargs for pd.DateOffset."""
        window = window.strip().upper()
        if window.endswith("M"):
            return {"months": int(window[:-1])}
        if window.endswith("D"):
            return {"days": int(window[:-1])}
        raise ValueError(f"race_window {window!r} must end with M (months) or D (days)")

    def _evaluate(self, result: MineResult, race_start: pd.Timestamp) -> float:
        """Compute the race metric on the recent window."""
        weekly = result.weekly_returns
        bench = result.benchmark_returns
        mask = weekly.index >= race_start
        w = weekly.loc[mask].dropna()
        b = bench.loc[mask].dropna()

        if len(w) < 4:
            return -float("inf")

        if self.metric == "sharpe":
            excess = w - 0.03 / 52
            std = excess.std()
            return (excess.mean() / std * math.sqrt(52)) if std > 0 else -float("inf")

        if self.metric == "information_ratio":
            common = w.index.intersection(b.index)
            if len(common) < 4:
                return -float("inf")
            diff = w.loc[common] - b.loc[common]
            std = diff.std()
            return (diff.mean() / std * math.sqrt(52)) if std > 0 else -float("inf")

        if self.metric == "annual_return_pct":
            total = (1.0 + w).prod() - 1.0
            years = max((w.index[-1] - w.index[0]).days / 365.25, 1e-6)
            return ((1.0 + total) ** (1.0 / years) - 1.0) * 100

        if self.metric == "calmar":
            equity = (1.0 + w).cumprod()
            total = equity.iloc[-1] / equity.iloc[0] - 1.0
            years = max((w.index[-1] - w.index[0]).days / 365.25, 1e-6)
            ann = ((1.0 + total) ** (1.0 / years) - 1.0) * 100
            running_max = equity.expanding().max()
            dd = (running_max - equity) / running_max
            max_dd = dd.max()
            if max_dd <= 0:
                return ann
            return ann / max_dd

        return -float("inf")
