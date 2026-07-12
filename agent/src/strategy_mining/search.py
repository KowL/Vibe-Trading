"""Walk-forward grid search over ``RollingICMiner`` hyperparameters.

The search loads the universe panel once, then for every parameter
combination it evaluates performance on a sequence of held-out validation
folds. The combination with the highest average validation score is then
re-trained on the full period to produce the final strategy config.
"""

from __future__ import annotations

import itertools
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


DEFAULT_PARAM_GRID: dict[str, list[Any]] = {
    "train_years": [2, 3],
    "top_n": [20, 30],
    "max_per_theme": [2, 3],
    "use_market_filter": [True, False],
}


@dataclass
class SearchResult:
    """Output of ``WalkForwardGridSearch.fit()``."""

    best_params: dict[str, Any]
    best_score: float
    scores: list[dict[str, Any]]
    final_result: MineResult
    metric: str = "sharpe"
    n_folds: int = 3

    def summary(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "n_folds": self.n_folds,
            "best_params": self.best_params,
            "best_score": self.best_score,
            "n_combinations": len(self.scores),
            "top_3": sorted(self.scores, key=lambda x: x["mean_score"], reverse=True)[:3],
        }

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.summary(), indent=2, default=str), encoding="utf-8")


def slice_panel(
    panel: dict[str, pd.DataFrame],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, pd.DataFrame]:
    """Return a panel sliced to ``[start, end]`` (inclusive) on the date index."""
    out: dict[str, pd.DataFrame] = {}
    for key, df in panel.items():
        if key.startswith("_") or not isinstance(df, pd.DataFrame):
            out[key] = df
            continue
        mask = (df.index >= start) & (df.index <= end)
        out[key] = df.loc[mask].copy()
    return out


class WalkForwardGridSearch:
    """Grid search with walk-forward cross-validation.

    Args:
        universe: ``csi300`` | ``sp500`` | ``btc-usdt``.
        period: ``YYYY-YYYY`` or ``YYYY-MM-DD/YYYY-MM-DD``.
        param_grid: Dict mapping parameter name to a list of values. Defaults
            to ``DEFAULT_PARAM_GRID``.
        n_folds: Number of validation folds (default 3). The last ``n_folds``
            full years in ``period`` are used as validation windows.
        metric: Validation metric. One of ``sharpe``, ``information_ratio``,
            ``annual_return_pct``, ``calmar``.
        min_train_years: Minimum training years required before the first fold.
        neutralize: Fixed neutralisation flag applied to every combination.
        neutralize_fields: Fixed neutralisation fields applied to every combination.
        market_cap_buckets: Fixed bucket count for market-cap neutralisation.
        panel: Optional pre-built panel to avoid reloading.
        registry: Optional registry (mostly for tests).
    """

    _METRICS: tuple[str, ...] = ("sharpe", "information_ratio", "annual_return_pct", "calmar")

    def __init__(
        self,
        universe: str = "csi300",
        period: str = "2020-2025",
        param_grid: dict[str, list[Any]] | None = None,
        n_folds: int = 3,
        metric: str = "sharpe",
        min_train_years: int = 2,
        neutralize: bool = False,
        neutralize_fields: list[str] | None = None,
        market_cap_buckets: int = 5,
        panel: dict[str, pd.DataFrame] | None = None,
        registry: Any | None = None,
    ) -> None:
        if metric not in self._METRICS:
            raise ValueError(f"metric {metric!r} not in {self._METRICS}")
        self.universe = universe
        self.period = period
        self.param_grid = param_grid if param_grid is not None else DEFAULT_PARAM_GRID
        self.n_folds = max(1, n_folds)
        self.metric = metric
        self.min_train_years = min_train_years
        self.neutralize = neutralize
        self.neutralize_fields = neutralize_fields or ["sector"]
        self.market_cap_buckets = max(2, market_cap_buckets)
        self._provided_panel = panel
        self._registry = registry

        self.panel: dict[str, pd.DataFrame] | None = None
        self._folds: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]] | None = None

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #

    def fit(self) -> SearchResult:
        """Run the grid search and return the best parameter set."""
        logger.info("strategy search: universe=%s period=%s", self.universe, self.period)
        self.panel = self._provided_panel or _load_universe_panel(self.universe, self.period)
        self._folds = self._make_folds(self.panel)
        if not self._folds:
            raise ValueError("not enough history to create walk-forward folds")

        combinations = self._param_product()
        logger.info("strategy search: evaluating %d combinations x %d folds", len(combinations), len(self._folds))

        scores: list[dict[str, Any]] = []
        for idx, params in enumerate(combinations, start=1):
            fold_scores = []
            logger.debug("strategy search: combination %d/%d %s", idx, len(combinations), params)
            for train_start, val_start, val_end in self._folds:
                fold_panel = slice_panel(self.panel, train_start, val_end)
                miner = RollingICMiner(
                    universe=self.universe,
                    period=self.period,
                    registry=self._registry,
                    panel=fold_panel,
                    **params,
                )
                result = miner.mine()
                score = self._validation_score(result, val_start, val_end)
                fold_scores.append(score)

            mean_score = float(np.mean(fold_scores))
            scores.append(
                {
                    "params": params,
                    "mean_score": round(mean_score, 4),
                    "fold_scores": [round(s, 4) for s in fold_scores],
                }
            )

        if not scores:
            raise RuntimeError("no parameter combinations evaluated")

        best_entry = max(scores, key=lambda x: x["mean_score"])
        best_params = best_entry["params"]
        best_score = best_entry["mean_score"]
        logger.info("strategy search: best %s=%.4f with %s", self.metric, best_score, best_params)

        final_miner = RollingICMiner(
            universe=self.universe,
            period=self.period,
            registry=self._registry,
            panel=self.panel,
            **best_params,
        )
        final_result = final_miner.mine()

        return SearchResult(
            best_params=best_params,
            best_score=best_score,
            scores=scores,
            final_result=final_result,
            metric=self.metric,
            n_folds=self.n_folds,
        )

    # --------------------------------------------------------------------- #
    # Helpers
    # --------------------------------------------------------------------- #

    def _param_product(self) -> list[dict[str, Any]]:
        """Generate the Cartesian product of the parameter grid."""
        keys = list(self.param_grid.keys())
        values = [self.param_grid[k] for k in keys]
        fixed = {
            "neutralize": self.neutralize,
            "neutralize_fields": self.neutralize_fields,
            "market_cap_buckets": self.market_cap_buckets,
        }
        return [{**dict(zip(keys, combo)), **fixed} for combo in itertools.product(*values)]

    def _make_folds(
        self,
        panel: dict[str, pd.DataFrame],
    ) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
        """Build walk-forward folds: (train_start, val_start, val_end)."""
        close = panel["close"]
        if close.empty:
            return []
        years = sorted(close.index.year.unique().tolist())
        # Need at least min_train_years before the first validation fold.
        if len(years) < self.min_train_years + self.n_folds:
            return []

        # Use the last n_folds years as validation, each one year long.
        val_years = years[-self.n_folds :]
        folds: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]] = []
        for val_year in val_years:
            val_start = pd.Timestamp(f"{val_year}-01-01")
            val_end = min(pd.Timestamp(f"{val_year}-12-31"), close.index.max())
            if val_start < close.index.min() or val_start > val_end:
                continue
            folds.append((close.index.min(), val_start, val_end))
        return folds

    def _validation_score(
        self,
        result: MineResult,
        val_start: pd.Timestamp,
        val_end: pd.Timestamp,
    ) -> float:
        """Compute the validation metric on the held-out window."""
        weekly = result.weekly_returns
        bench = result.benchmark_returns
        mask = (weekly.index >= val_start) & (weekly.index <= val_end)
        val_weekly = weekly.loc[mask].dropna()
        val_bench = bench.loc[mask].dropna()

        if len(val_weekly) < 4:
            return -float("inf")

        if self.metric == "sharpe":
            excess = val_weekly - 0.03 / 52
            std = excess.std()
            return (excess.mean() / std * math.sqrt(52)) if std > 0 else -float("inf")

        if self.metric == "information_ratio":
            common = val_weekly.index.intersection(val_bench.index)
            if len(common) < 4:
                return -float("inf")
            active = val_weekly.loc[common]
            passive = val_bench.loc[common]
            diff = active - passive
            std = diff.std()
            return (diff.mean() / std * math.sqrt(52)) if std > 0 else -float("inf")

        if self.metric == "annual_return_pct":
            total = (1.0 + val_weekly).prod() - 1.0
            years = max((val_end - val_start).days / 365.25, 1e-6)
            return ((1.0 + total) ** (1.0 / years) - 1.0) * 100

        if self.metric == "calmar":
            equity = (1.0 + val_weekly).cumprod()
            total = equity.iloc[-1] / equity.iloc[0] - 1.0
            years = max((val_end - val_start).days / 365.25, 1e-6)
            ann = ((1.0 + total) ** (1.0 / years) - 1.0) * 100
            running_max = equity.expanding().max()
            dd = (running_max - equity) / running_max
            max_dd = dd.max()
            if max_dd <= 0:
                # No drawdown: Calmar is infinity in theory; return annual return.
                return ann
            return ann / max_dd

        return -float("inf")
