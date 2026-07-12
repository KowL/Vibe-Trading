"""Unit tests for the baseline rolling-IC strategy miner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.factors.registry import Registry
from src.strategy_mining.miner import RollingICMiner


@dataclass
class _FakeAlpha:
    alpha_id: str
    theme: str
    factor_value: float | None = None
    noise: bool = False


class _FakeRegistry:
    """Minimal registry stand-in for miner tests."""

    def __init__(self, alphas: list[_FakeAlpha], panel: dict[str, pd.DataFrame]) -> None:
        self.alphas = {a.alpha_id: a for a in alphas}
        self.panel = panel
        self.close = panel["close"]
        # Precompute future average return per stock for the "predictive" alpha.
        self.fwd = self.close.shift(-5) / self.close - 1.0
        self.rng = np.random.default_rng(123)

    def list(self) -> list[str]:
        return sorted(self.alphas)

    def get(self, alpha_id: str) -> Any:
        return _AlphaStub(alpha_id, self.alphas[alpha_id].theme)

    def compute(self, alpha_id: str, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        alpha = self.alphas[alpha_id]
        if alpha.noise:
            # Pure noise: no cross-sectional predictive power.
            return pd.DataFrame(
                self.rng.normal(0.0, 1.0, size=panel["close"].shape),
                index=panel["close"].index,
                columns=panel["close"].columns,
            )
        if alpha.factor_value is not None:
            return pd.DataFrame(
                alpha.factor_value,
                index=panel["close"].index,
                columns=panel["close"].columns,
            )
        # Predictive alpha: today's factor = future 5-day return rank proxy.
        # This is intentionally a test-only oracle so the pipeline produces
        # a non-random signal and passes the alive gate.
        return self.fwd.reindex(index=panel["close"].index, columns=panel["close"].columns)


@dataclass
class _AlphaStub:
    id: str
    meta: dict[str, Any]

    def __init__(self, alpha_id: str, theme: str) -> None:
        self.id = alpha_id
        self.meta = {"theme": [theme], "formula_latex": "test"}


def _make_panel(n_days: int = 260, n_stocks: int = 5, seed: int = 42) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-02", periods=n_days, freq="B")
    codes = [f"00000{i}.SZ" for i in range(1, n_stocks + 1)]

    # Persistent cross-sectional drift: higher-index stocks grow faster.
    close = pd.DataFrame(index=dates, columns=codes, dtype=float)
    for i, code in enumerate(codes):
        daily_drift = 0.0005 * (i + 1)
        close[code] = 10.0 * (1.0 + daily_drift) ** np.arange(n_days) * (
            1.0 + rng.normal(0.0, 0.01, n_days)
        )

    panel: dict[str, pd.DataFrame] = {}
    for field in ("open", "high", "low", "close"):
        panel[field] = close * (1.0 + rng.normal(0.0, 0.005, close.shape))
    panel["close"] = close
    panel["volume"] = pd.DataFrame(
        rng.integers(1_000_000, 10_000_000, size=close.shape),
        index=dates,
        columns=codes,
    )
    panel["amount"] = panel["volume"] * close * rng.uniform(0.9, 1.1, size=close.shape)
    panel["vwap"] = panel["amount"] / panel["volume"]
    return panel


def test_weekly_forward_returns_and_rebalance_dates() -> None:
    panel = _make_panel(n_days=100, n_stocks=3)
    miner = RollingICMiner(registry=Registry(zoo_root=Path("/nonexistent")))  # empty registry
    weekly = miner._compute_weekly_forward_returns(panel)
    dates = miner._rebalance_dates(panel, weekly)

    assert weekly.shape == panel["close"].shape
    assert not dates.empty
    # Rebalance dates are Fridays (or last available trading day before).
    assert all(d.weekday() < 5 for d in dates)


def test_mine_with_fake_registry() -> None:
    panel = _make_panel(n_days=400, n_stocks=5)
    alphas = [
        _FakeAlpha("alpha_momentum", "momentum"),
        _FakeAlpha("alpha_reversal", "reversal", factor_value=-0.1),
    ]
    registry = _FakeRegistry(alphas, panel)

    miner = RollingICMiner(
        universe="csi300",
        period="2020-2020",
        train_years=1,
        top_n=3,
        max_per_theme=2,
        min_ic=0.01,
        min_ic_positive_ratio=0.52,
        min_t_stat=1.5,
        use_market_filter=False,
        registry=registry,
    )

    # Bypass the real universe loader so the test runs offline.
    with patch("src.strategy_mining.miner._load_universe_panel", return_value=panel):
        result = miner.mine()

    assert result.config.universe == "csi300"
    assert result.config.top_n == 3
    assert result.portfolios
    for d, syms in result.portfolios.items():
        assert len(syms) <= 3

    assert len(result.equity_curve) > 1
    assert result.metrics["annual_return_pct"] is not None
    assert result.metrics["sharpe"] is not None
    assert result.metrics["max_drawdown_pct"] >= 0.0
    assert "information_ratio" in result.metrics


def test_select_alphas_theme_balancing() -> None:
    panel = _make_panel(n_days=50, n_stocks=3)
    miner = RollingICMiner(registry=Registry(zoo_root=Path("/nonexistent")))
    stats = {
        "a1": {"theme": "momentum", "ir": 0.5, "ic_mean": 0.03},
        "a2": {"theme": "momentum", "ir": 0.4, "ic_mean": 0.03},
        "a3": {"theme": "momentum", "ir": 0.1, "ic_mean": 0.03},
        "b1": {"theme": "value", "ir": 0.6, "ic_mean": 0.04},
    }
    miner.max_per_theme = 2
    selected, alpha_themes, theme_weights = miner._select_alphas_for_date(
        pd.Timestamp("2020-06-01"), stats
    )

    assert len(selected) <= 4
    assert "a1" in selected
    assert "b1" in selected
    assert alpha_themes["a1"] == "momentum"
    assert alpha_themes["b1"] == "value"
    assert set(theme_weights) == {"momentum", "value"}


def test_score_stocks_composite_percentiles() -> None:
    panel = _make_panel(n_days=50, n_stocks=3)
    miner = RollingICMiner(registry=Registry(zoo_root=Path("/nonexistent")))
    miner.panel = panel

    d = panel["close"].index[30]
    alpha_values = {
        "a1": pd.Series([3.0, 2.0, 1.0], index=panel["close"].columns),
        "a2": pd.Series([1.0, 3.0, 2.0], index=panel["close"].columns),
    }
    alpha_themes = {"a1": "momentum", "a2": "value"}
    theme_weights = {"momentum": 0.5, "value": 0.5}
    selected = ["a1", "a2"]

    scores = miner._score_stocks_for_date(
        d, selected, alpha_themes, theme_weights, alpha_values
    )

    assert scores is not None
    assert len(scores) == 3
    # a1 ranks first stock highest, a2 ranks second stock highest.
    assert scores["000001.SZ"] > scores["000003.SZ"]


def test_strict_gate_excludes_noise_alpha() -> None:
    panel = _make_panel(n_days=400, n_stocks=5)
    alphas = [
        _FakeAlpha("alpha_predictive", "momentum"),
        _FakeAlpha("alpha_noise", "value", noise=True),
    ]
    registry = _FakeRegistry(alphas, panel)

    miner = RollingICMiner(
        universe="csi300",
        period="2020-2020",
        train_years=1,
        top_n=3,
        max_per_theme=2,
        min_ic=0.01,
        min_ic_positive_ratio=0.52,
        min_t_stat=1.5,
        use_market_filter=False,
        use_random_control=True,
        n_random_seeds=3,
        alpha_t_threshold=2.0,
        registry=registry,
    )

    with patch("src.strategy_mining.miner._load_universe_panel", return_value=panel):
        result = miner.mine()

    # The predictive oracle should survive the strict gate; noise should not.
    assert "alpha_predictive" in result.config.selected_alphas
    assert "alpha_noise" not in result.config.selected_alphas


def test_neutralizer_demean_within_groups() -> None:
    from src.strategy_mining.neutralization import Neutralizer

    scores = pd.Series({"a1": 0.9, "a2": 0.8, "a3": 0.7, "b1": 0.3, "b2": 0.2, "b3": 0.1})
    groups = pd.Series({"a1": "A", "a2": "A", "a3": "A", "b1": "B", "b2": "B", "b3": "B"})
    neutral = Neutralizer(fields=["sector"])
    out = neutral._demean_within_groups(scores, groups)

    # Means within groups are removed; cross-group ordering should still hold.
    assert out["a1"] > out["a3"]
    assert out["b1"] > out["b3"]
    # The best relative stock in B can now compete with the best in A.
    assert out["b1"] > out["a2"]


def test_mine_with_neutralization_balances_sectors() -> None:
    panel = _make_panel(n_days=400, n_stocks=6, seed=7)
    codes = list(panel["close"].columns)
    sectors = pd.DataFrame(
        {code: ("A" if i < 3 else "B") for i, code in enumerate(codes)},
        index=panel["close"].index,
    )
    panel["sector"] = sectors

    # Alpha value = stock index: stocks 3-5 (sector B) dominate without neutralisation.
    class _SectorAwareRegistry:
        def __init__(self, codes: list[str]) -> None:
            self._codes = codes

        def list(self) -> list[str]:
            return ["alpha_rank"]

        def get(self, alpha_id: str) -> Any:
            from tests.strategy_mining.test_miner import _AlphaStub
            return _AlphaStub(alpha_id, "momentum")

        def compute(self, alpha_id: str, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
            return pd.DataFrame(
                {code: float(i + 1) for i, code in enumerate(self._codes)},
                index=panel["close"].index,
                columns=self._codes,
            )

    registry = _SectorAwareRegistry(codes)

    # Without neutralisation: Top 3 should all be sector B.
    miner_plain = RollingICMiner(
        universe="csi300",
        period="2020-2020",
        train_years=1,
        top_n=3,
        max_per_theme=1,
        min_ic=0.001,
        min_ic_positive_ratio=0.51,
        min_t_stat=1.0,
        use_market_filter=False,
        neutralize=False,
        registry=registry,
    )
    with patch("src.strategy_mining.miner._load_universe_panel", return_value=panel):
        result_plain = miner_plain.mine()

    last_portfolio = list(result_plain.portfolios.values())[-1]
    assert all(panel["sector"].iloc[-1][s] == "B" for s in last_portfolio)

    # With sector neutralisation: Top 3 should include at least one sector A stock.
    miner_neutral = RollingICMiner(
        universe="csi300",
        period="2020-2020",
        train_years=1,
        top_n=3,
        max_per_theme=1,
        min_ic=0.001,
        min_ic_positive_ratio=0.51,
        min_t_stat=1.0,
        use_market_filter=False,
        neutralize=True,
        neutralize_fields=["sector"],
        registry=registry,
    )
    with patch("src.strategy_mining.miner._load_universe_panel", return_value=panel):
        result_neutral = miner_neutral.mine()

    last_portfolio_neutral = list(result_neutral.portfolios.values())[-1]
    sectors_in_portfolio = {panel["sector"].iloc[-1][s] for s in last_portfolio_neutral}
    assert "A" in sectors_in_portfolio


def test_backtest_turnover_costs() -> None:
    panel = _make_panel(n_days=100, n_stocks=5)
    miner = RollingICMiner(
        universe="csi300",
        period="2020-2020",
        registry=Registry(zoo_root=Path("/nonexistent")),
        use_market_filter=False,
    )
    miner.panel = panel
    miner.weekly_returns = miner._compute_weekly_forward_returns(panel)
    miner.rebalance_dates = miner._rebalance_dates(panel, miner.weekly_returns)

    # Alternate between two disjoint portfolios to force turnover.
    portfolios: dict[pd.Timestamp, list[str]] = {}
    stocks = list(panel["close"].columns)
    for i, d in enumerate(miner.rebalance_dates):
        portfolios[d] = stocks[:2] if i % 2 == 0 else stocks[2:4]

    equity, weekly, bench = miner._backtest(portfolios)
    assert len(equity) == len(miner.rebalance_dates) + 1
    assert len(weekly) == len(miner.rebalance_dates)
    assert len(bench) == len(miner.rebalance_dates)
