"""Baseline rolling-IC strategy miner.

Builds a multi-factor strategy from the Alpha Zoo by:

1. Loading a universe-wide panel (currently csi300 / sp500 / btc-usdt).
2. Computing weekly forward returns.
3. For each rebalance date, using a rolling training window to select
   "alive" alphas (IC mean, positive-ratio, t-stat gates).
4. Balancing selected alphas by theme.
5. Scoring stocks with theme-weighted percentiles of the selected alphas.
6. Holding an equal-weight portfolio of the top-N stocks.
7. Running a simplified weekly-rebalance backtest with turnover costs.

This is intentionally a baseline: no machine learning, no non-linear
interactions, no sector neutralisation. The goal is a reproducible
end-to-end skeleton that more sophisticated miners can extend.
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

from src.factors.bench_runner_strict import (
    StrictThresholds,
    alpha_series_paired,
    categorise_strict,
    compute_random_ic_series,
    t_stat,
)
from src.factors.factor_analysis_core import compute_ic_series
from src.factors.registry import RegistryError, SkipAlpha, get_default_registry
from src.strategy_mining.metadata import enrich_panel
from src.strategy_mining.neutralization import Neutralizer
from src.tools.alpha_bench_tool import _load_universe_panel

logger = logging.getLogger(__name__)


@dataclass
class StrategyConfig:
    """Serializable configuration of a mined strategy."""

    name: str = "rolling_ic_csi300"
    universe: str = "csi300"
    period: str = "2020-2025"
    rebalance_freq: str = "weekly"
    train_years: int = 3
    top_n: int = 30
    max_per_theme: int = 3
    min_ic: float = 0.02
    min_ic_positive_ratio: float = 0.55
    min_t_stat: float = 2.0
    commission: float = 0.0003
    slippage: float = 0.001
    use_market_filter: bool = True
    use_random_control: bool = False
    n_random_seeds: int = 5
    alpha_t_threshold: float = 2.0
    neutralize: bool = False
    neutralize_fields: list[str] = field(default_factory=lambda: ["sector"])
    market_cap_buckets: int = 5
    replacement_buffer: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    latest_weights: dict[str, float] = field(default_factory=dict)
    latest_theme_weights: dict[str, float] = field(default_factory=dict)
    selected_alphas: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "universe": self.universe,
            "period": self.period,
            "rebalance_freq": self.rebalance_freq,
            "train_years": self.train_years,
            "top_n": self.top_n,
            "max_per_theme": self.max_per_theme,
            "min_ic": self.min_ic,
            "min_ic_positive_ratio": self.min_ic_positive_ratio,
            "min_t_stat": self.min_t_stat,
            "commission": self.commission,
            "slippage": self.slippage,
            "use_market_filter": self.use_market_filter,
            "use_random_control": self.use_random_control,
            "n_random_seeds": self.n_random_seeds,
            "alpha_t_threshold": self.alpha_t_threshold,
            "neutralize": self.neutralize,
            "neutralize_fields": self.neutralize_fields,
            "market_cap_buckets": self.market_cap_buckets,
            "replacement_buffer": self.replacement_buffer,
            "created_at": self.created_at,
            "latest_weights": self.latest_weights,
            "latest_theme_weights": self.latest_theme_weights,
            "selected_alphas": self.selected_alphas,
        }

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")


@dataclass
class MineResult:
    """Output of ``RollingICMiner.mine()``."""

    config: StrategyConfig
    portfolios: dict[pd.Timestamp, list[str]] = field(default_factory=dict)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    metrics: dict[str, float] = field(default_factory=dict)
    composite_ic_series: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    weekly_returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    benchmark_returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    report: dict[str, Any] = field(default_factory=dict)


class RollingICMiner:
    """Mine a rolling-IC multi-factor strategy from the Alpha Zoo.

    Args:
        universe: ``csi300`` | ``sp500`` | ``btc-usdt``.
        period: ``YYYY-YYYY`` or ``YYYY-MM-DD/YYYY-MM-DD``.
        train_years: Length of the rolling training window in years.
        rebalance_freq: Only ``weekly`` is implemented in the baseline.
        top_n: Number of stocks in the equal-weight portfolio.
        max_per_theme: Theme-diversity cap when selecting alphas.
        min_ic: Minimum mean IC for an alpha to be considered alive.
        min_ic_positive_ratio: Minimum IC-positive ratio for an alive alpha.
        min_t_stat: Minimum |t-stat| for an alive alpha.
        commission: One-side commission as a fraction of traded value.
        slippage: One-side slippage as a fraction of traded value.
        use_market_filter: If True, only take new positions when the equal-weight
            market index is in an uptrend (MA20 > MA60).
        use_random_control: If True, use a same-universe row-shuffled random
            control and require the alpha to be ``confirmed_alive`` under the
            strict gate before it can enter the strategy.
        n_random_seeds: Number of random shuffles when ``use_random_control``
            is enabled.
        alpha_t_threshold: t-stat threshold for the strict random-control gate.
        neutralize: If True, neutralise composite scores by sector / market-cap
            group before selecting Top-N.
        neutralize_fields: List of panel fields to neutralise on, e.g.
            ``["sector"]`` or ``["sector", "market_cap"]``.
        market_cap_buckets: Number of buckets when neutralising on market cap.
        registry: Optional pre-built Alpha Zoo registry (mostly for tests).
    """

    REBALANCE_HORIZON: int = 5  # weekly hold

    def __init__(
        self,
        universe: str = "csi300",
        period: str = "2020-2025",
        train_years: int = 3,
        rebalance_freq: str = "weekly",
        top_n: int = 30,
        max_per_theme: int = 3,
        min_ic: float = 0.02,
        min_ic_positive_ratio: float = 0.55,
        min_t_stat: float = 2.0,
        commission: float = 0.0003,
        slippage: float = 0.001,
        use_market_filter: bool = True,
        use_random_control: bool = False,
        n_random_seeds: int = 5,
        alpha_t_threshold: float = 2.0,
        neutralize: bool = False,
        neutralize_fields: list[str] | None = None,
        market_cap_buckets: int = 5,
        replacement_buffer: float = 0.0,
        registry: Any | None = None,
        panel: dict[str, pd.DataFrame] | None = None,
    ) -> None:
        if rebalance_freq != "weekly":
            raise NotImplementedError("baseline miner only supports rebalance_freq='weekly'")
        self.universe = universe
        self.period = period
        self.train_years = train_years
        self.rebalance_freq = rebalance_freq
        self.top_n = top_n
        self.max_per_theme = max_per_theme
        self.min_ic = min_ic
        self.min_ic_positive_ratio = min_ic_positive_ratio
        self.min_t_stat = min_t_stat
        self.commission = commission
        self.slippage = slippage
        self.use_market_filter = use_market_filter
        self.use_random_control = use_random_control
        self.n_random_seeds = max(1, n_random_seeds)
        self.alpha_t_threshold = alpha_t_threshold
        self.neutralize = neutralize
        self.neutralize_fields = neutralize_fields or ["sector"]
        self.market_cap_buckets = max(2, market_cap_buckets)
        self.replacement_buffer = max(0.0, replacement_buffer)
        self.registry = registry if registry is not None else get_default_registry()

        # Optional pre-built panel (used by grid search to avoid reloading).
        self._provided_panel = panel

        # Populated in ``mine()``.
        self.panel: dict[str, pd.DataFrame] | None = None
        self.weekly_returns: pd.DataFrame | None = None
        self.rebalance_dates: pd.DatetimeIndex | None = None

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #

    def mine(self) -> MineResult:
        """Run the full mining pipeline and return results + config."""
        logger.info("strategy mine: universe=%s period=%s", self.universe, self.period)
        if self._provided_panel is not None:
            self.panel = self._provided_panel
        else:
            self.panel = _load_universe_panel(self.universe, self.period)
        if self.neutralize:
            self.panel = enrich_panel(self.panel, self.universe)
        self.weekly_returns = self._compute_weekly_forward_returns(self.panel)
        self.rebalance_dates = self._rebalance_dates(self.panel, self.weekly_returns)

        if len(self.rebalance_dates) == 0:
            raise ValueError("no valid weekly rebalance dates in panel")

        alpha_ids = self.registry.list()
        if not alpha_ids:
            raise RuntimeError("Alpha Zoo registry is empty")

        # Per-date containers populated while scanning alphas.
        date_alpha_values: dict[pd.Timestamp, dict[str, pd.Series]] = {
            d: {} for d in self.rebalance_dates
        }
        date_alpha_stats: dict[pd.Timestamp, dict[str, dict[str, Any]]] = {
            d: {} for d in self.rebalance_dates
        }

        for idx, aid in enumerate(alpha_ids, start=1):
            try:
                self._process_alpha(
                    aid,
                    date_alpha_values,
                    date_alpha_stats,
                )
            except (SkipAlpha, RegistryError, RuntimeError, KeyError, ValueError) as exc:
                logger.debug("skip alpha %s: %s", aid, exc)
            except Exception as exc:  # noqa: BLE001
                logger.exception("unexpected failure on alpha %s", aid)
            if idx % 50 == 0:
                logger.info("strategy mine: processed %d/%d alphas", idx, len(alpha_ids))

        # Build portfolios from per-date scores.
        portfolios: dict[pd.Timestamp, list[str]] = {}
        composite_scores: dict[pd.Timestamp, pd.Series] = {}
        latest_selected_alphas: list[str] = []

        latest_alpha_themes: dict[str, str] = {}
        latest_theme_weights: dict[str, float] = {}
        prev_date: pd.Timestamp | None = None
        for d in self.rebalance_dates:
            selected, alpha_themes, theme_weights = self._select_alphas_for_date(
                d,
                date_alpha_stats[d],
            )
            if selected:
                latest_selected_alphas = selected
                latest_alpha_themes = alpha_themes
                latest_theme_weights = theme_weights

            scores = self._score_stocks_for_date(
                d,
                selected,
                alpha_themes,
                theme_weights,
                date_alpha_values[d],
            )
            composite_scores[d] = scores
            if scores is not None and not scores.empty:
                portfolios[d] = self._select_top_n_sticky(scores, portfolios.get(prev_date))
            else:
                portfolios[d] = []
            prev_date = d

        # Weekly backtest.
        equity_curve, weekly_returns_strategy, benchmark_returns = self._backtest(portfolios)

        # Composite signal IC series (for diagnostics).
        composite_ic_series = self._composite_ic(composite_scores)

        metrics = self._compute_metrics(equity_curve, weekly_returns_strategy, benchmark_returns)

        config = StrategyConfig(
            name=f"rolling_ic_{self.universe}",
            universe=self.universe,
            period=self.period,
            rebalance_freq=self.rebalance_freq,
            train_years=self.train_years,
            top_n=self.top_n,
            max_per_theme=self.max_per_theme,
            min_ic=self.min_ic,
            min_ic_positive_ratio=self.min_ic_positive_ratio,
            min_t_stat=self.min_t_stat,
            commission=self.commission,
            slippage=self.slippage,
            use_market_filter=self.use_market_filter,
            use_random_control=self.use_random_control,
            n_random_seeds=self.n_random_seeds,
            alpha_t_threshold=self.alpha_t_threshold,
            neutralize=self.neutralize,
            neutralize_fields=self.neutralize_fields,
            market_cap_buckets=self.market_cap_buckets,
            selected_alphas=sorted(set(latest_selected_alphas)),
            latest_theme_weights=latest_theme_weights,
        )

        report = {
            "universe": self.universe,
            "period": self.period,
            "n_rebalances": len(self.rebalance_dates),
            "n_alphas_in_registry": len(alpha_ids),
            "metrics": metrics,
            "portfolios": {
                d.strftime("%Y-%m-%d"): syms for d, syms in portfolios.items()
            },
        }

        return MineResult(
            config=config,
            portfolios=portfolios,
            equity_curve=equity_curve,
            metrics=metrics,
            composite_ic_series=composite_ic_series,
            weekly_returns=weekly_returns_strategy,
            benchmark_returns=benchmark_returns,
            report=report,
        )

    # --------------------------------------------------------------------- #
    # Data preparation
    # --------------------------------------------------------------------- #

    @staticmethod
    def _compute_weekly_forward_returns(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Weekly forward simple returns aligned to the current bar."""
        import numpy as np

        close = panel.get("close")
        if close is None or close.empty:
            raise ValueError("panel missing 'close'")
        # close[t+5] / close[t] - 1; NaN where future is unavailable.
        rets = close.shift(-RollingICMiner.REBALANCE_HORIZON) / close - 1.0
        # Guard against data-source zeros / infs that would poison mean returns.
        return rets.replace([np.inf, -np.inf], np.nan)

    @staticmethod
    def _rebalance_dates(
        panel: dict[str, pd.DataFrame],
        weekly_returns: pd.DataFrame,
    ) -> pd.DatetimeIndex:
        """Return Fridays that exist in the panel and have future returns."""
        close = panel["close"]
        start = close.index.min()
        end = close.index.max()
        # Keep enough future bars for the hold horizon.
        valid_end = close.index[-(RollingICMiner.REBALANCE_HORIZON + 1)]
        fridays = pd.date_range(start=start, end=end, freq="W-FRI")
        locs = close.index.searchsorted(fridays, side="right") - 1
        asof = close.index[locs]
        asof = asof[locs >= 0]
        asof = asof[(asof >= start) & (asof <= valid_end)]
        return pd.DatetimeIndex(sorted(asof.unique()))

    # --------------------------------------------------------------------- #
    # Per-alpha processing
    # --------------------------------------------------------------------- #

    def _process_alpha(
        self,
        alpha_id: str,
        date_alpha_values: dict[pd.Timestamp, dict[str, pd.Series]],
        date_alpha_stats: dict[pd.Timestamp, dict[str, dict[str, Any]]],
    ) -> None:
        """Compute one alpha, then update per-date value/stat containers."""
        factor_df = self.registry.compute(alpha_id, self.panel)
        if factor_df is None or factor_df.empty:
            return

        # Whole-period IC series vs weekly returns.
        ic_series = compute_ic_series(factor_df, self.weekly_returns)
        if ic_series.empty:
            return

        # Optional same-universe random control for the strict gate.
        random_ic: pd.Series | None = None
        if self.use_random_control:
            random_ic = compute_random_ic_series(
                factor_df,
                self.weekly_returns,
                n_seeds=self.n_random_seeds,
            )
            if random_ic is None or random_ic.empty:
                return

        meta = self.registry.get(alpha_id).meta or {}
        theme = (meta.get("theme") or ["uncategorised"])[0]
        thresholds = StrictThresholds(alpha_t_threshold=self.alpha_t_threshold)

        for d in self.rebalance_dates:
            train_start = d - pd.DateOffset(years=self.train_years)
            train_mask = (ic_series.index > train_start) & (ic_series.index <= d)
            train_ic = ic_series[train_mask]
            if len(train_ic) < 30:
                continue

            ic_mean = float(train_ic.mean())
            pos_ratio = float((train_ic > 0).mean())

            if self.use_random_control:
                train_random = random_ic[train_mask]
                alpha_full = alpha_series_paired(train_ic, train_random)
                if alpha_full.empty or len(alpha_full) < 30:
                    continue
                alpha_t_full = t_stat(alpha_full)
                category = categorise_strict(
                    {
                        "alpha_t_full": alpha_t_full,
                        "alpha_t_train": None,
                        "alpha_t_test": None,
                        "ic_count": len(alpha_full),
                    },
                    thresholds,
                )
                if category != "confirmed_alive":
                    continue
                ts = alpha_t_full
            else:
                ts = t_stat(train_ic)
                if not (
                    ic_mean > self.min_ic
                    and pos_ratio >= self.min_ic_positive_ratio
                    and abs(ts) > self.min_t_stat
                ):
                    continue

            # Capture the latest factor value at or before d.
            value_row = self._latest_row(factor_df, d)
            if value_row is None or value_row.dropna().empty:
                continue

            date_alpha_values[d][alpha_id] = value_row
            date_alpha_stats[d][alpha_id] = {
                "ic_mean": ic_mean,
                "ic_std": float(train_ic.std()),
                "ir": ic_mean / float(train_ic.std()) if train_ic.std() > 0 else 0.0,
                "ic_positive_ratio": pos_ratio,
                "t_stat": ts,
                "theme": theme,
                "n": len(train_ic),
            }

    @staticmethod
    def _latest_row(df: pd.DataFrame, asof: pd.Timestamp) -> pd.Series | None:
        """Return the last row at or before ``asof``; None if unavailable."""
        if asof < df.index.min():
            return None
        idx = df.index.asof(asof)
        if idx is pd.NaT or idx is None:
            return None
        row = df.loc[idx]
        if isinstance(row, pd.DataFrame):
            # Duplicate index edge case.
            row = row.iloc[-1]
        return row

    # --------------------------------------------------------------------- #
    # Alpha selection + stock scoring
    # --------------------------------------------------------------------- #

    def _select_alphas_for_date(
        self,
        d: pd.Timestamp,
        stats: dict[str, dict[str, Any]],
    ) -> tuple[list[str], dict[str, str], dict[str, float]]:
        """Select a theme-balanced set of alive alphas for ``d``.

        Returns:
            (selected_alpha_ids, alpha_themes, theme_weights) where
            ``alpha_themes`` maps alpha id to its primary theme and
            ``theme_weights`` are mean-IC-based weights for each theme.
        """
        if not stats:
            return [], {}, {}

        # Group by primary theme, take top ``max_per_theme`` by IR.
        by_theme: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for aid, s in stats.items():
            by_theme.setdefault(s["theme"], []).append((aid, s))

        selected: list[str] = []
        alpha_themes: dict[str, str] = {}
        theme_weights: dict[str, float] = {}
        for theme, items in by_theme.items():
            items_sorted = sorted(items, key=lambda x: x[1]["ir"], reverse=True)
            picked = items_sorted[: self.max_per_theme]
            if not picked:
                continue
            for aid, s in picked:
                selected.append(aid)
                alpha_themes[aid] = theme
            theme_weights[theme] = float(np.mean([s["ic_mean"] for _, s in picked]))

        return selected, alpha_themes, theme_weights

    def _score_stocks_for_date(
        self,
        d: pd.Timestamp,
        selected: list[str],
        alpha_themes: dict[str, str],
        theme_weights: dict[str, float],
        alpha_values: dict[str, pd.Series],
    ) -> pd.Series | None:
        """Compute theme-weighted composite percentile score per stock."""
        if not selected or not theme_weights:
            return None

        # Normalise theme weights to sum to one (use only themes present today).
        total_w = sum(abs(w) for w in theme_weights.values())
        if total_w <= 0:
            return None
        norm_theme_weights = {t: w / total_w for t, w in theme_weights.items()}

        # Per-alpha percentile ranks (higher value -> higher percentile).
        theme_scores: dict[str, list[pd.Series]] = {t: [] for t in norm_theme_weights}
        for aid in selected:
            values = alpha_values.get(aid)
            if values is None or values.dropna().empty:
                continue
            theme = alpha_themes.get(aid, "uncategorised")
            if theme not in theme_scores:
                continue
            rank = values.rank(method="average", pct=True)
            theme_scores[theme].append(rank)

        if not any(theme_scores.values()):
            return None

        composite = pd.Series(0.0, index=self.panel["close"].columns)
        for theme, series_list in theme_scores.items():
            if not series_list:
                continue
            avg_pct = pd.concat(series_list, axis=1).mean(axis=1)
            composite = composite.add(avg_pct * norm_theme_weights.get(theme, 0.0), fill_value=0.0)

        if self.neutralize:
            neutralizer = Neutralizer(
                fields=self.neutralize_fields,
                market_cap_buckets=self.market_cap_buckets,
            )
            composite = neutralizer.neutralize(composite, self.panel, d)

        return composite

    def _select_top_n_sticky(
        self,
        scores: pd.Series,
        previous: list[str] | None,
    ) -> list[str]:
        """Select Top-N stocks with a sticky replacement buffer.

        If ``replacement_buffer > 0``, existing holdings whose composite score
        is within ``buffer`` of the new Top-N cutoff are kept.  A new stock
        must be *significantly* better than the marginal incumbent before it
        triggers a trade, which reduces turnover while preserving the bulk of
        the signal.
        """
        ranked = scores.dropna().sort_values(ascending=False)
        if ranked.empty:
            return []
        if not previous or self.replacement_buffer <= 0.0 or len(ranked) < self.top_n:
            return ranked.head(self.top_n).index.tolist()

        cutoff = ranked.iloc[self.top_n - 1]
        # Keep previous holdings that are still close to the new cutoff.
        kept = [
            code
            for code in previous
            if code in ranked.index and ranked[code] >= cutoff - self.replacement_buffer
        ]
        # Fill remaining slots with the best new candidates not already kept.
        remaining = [code for code in ranked.index if code not in kept]
        target = kept + remaining
        return target[: self.top_n]

    # --------------------------------------------------------------------- #
    # Backtest
    # --------------------------------------------------------------------- #

    def _backtest(
        self,
        portfolios: dict[pd.Timestamp, list[str]],
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """Vectorised weekly-rebalance backtest with turnover costs.

        Returns:
            (equity curve, strategy weekly returns, equal-weight benchmark
            weekly returns on the same rebalance dates).
        """
        close = self.panel["close"]
        market = close.mean(axis=1)
        bullish = market.rolling(20).mean() > market.rolling(60).mean()

        portfolio_values = [1.0]
        portfolio_dates = [self.rebalance_dates[0] - pd.Timedelta(days=7)]
        current: set[str] = set()
        weekly_rets: dict[pd.Timestamp, float] = {}
        bench_rets: dict[pd.Timestamp, float] = {}

        one_side_cost = self.commission + self.slippage

        for d in self.rebalance_dates:
            target = set(portfolios.get(d, []))
            week_bar = self.weekly_returns.loc[d].replace([np.inf, -np.inf], np.nan)
            bench_rets[d] = float(week_bar.mean(skipna=True))

            # Market filter: if bearish (or not enough history), move to cash.
            if self.use_market_filter:
                bull_flag = bullish.asof(d)
                if bull_flag is None or (isinstance(bull_flag, float) and pd.isna(bull_flag)) or not bool(bull_flag):
                    target = set()

            if not target:
                # Hold cash; no return, but we still pay costs to exit old positions.
                turnover = len(current) / self.top_n if self.top_n else 0.0
                cost = turnover * 2.0 * one_side_cost
                ret = -cost
                current = set()
            else:
                # Weekly return of an equal-weight target portfolio.
                available = [c for c in target if c in week_bar.index]
                ret = float(week_bar[available].mean(skipna=True)) if available else 0.0

                # Turnover cost: round-trip on changed names.
                entered = target - current
                exited = current - target
                turnover = (len(entered) + len(exited)) / self.top_n
                cost = turnover * 2.0 * one_side_cost
                ret = ret - cost
                current = target

            weekly_rets[d] = ret
            portfolio_values.append(portfolio_values[-1] * (1.0 + ret))
            portfolio_dates.append(d)

        equity = pd.Series(portfolio_values, index=portfolio_dates)
        weekly = pd.Series(weekly_rets)
        bench = pd.Series(bench_rets)
        return equity, weekly, bench

    # --------------------------------------------------------------------- #
    # Diagnostics
    # --------------------------------------------------------------------- #

    def _composite_ic(
        self,
        composite_scores: dict[pd.Timestamp, pd.Series],
    ) -> pd.Series:
        """IC series of the composite score vs weekly returns."""
        score_df = pd.DataFrame({d: s for d, s in composite_scores.items() if s is not None}).T
        return compute_ic_series(score_df, self.weekly_returns)

    @staticmethod
    def _compute_metrics(
        equity: pd.Series,
        weekly_returns: pd.Series,
        benchmark_returns: pd.Series,
    ) -> dict[str, float]:
        """Return annualised return, Sharpe, max drawdown, IR vs equal-weight bench."""
        equity = equity.dropna()
        weekly_returns = weekly_returns.dropna()
        if len(equity) < 2:
            return {
                "annual_return_pct": 0.0,
                "sharpe": 0.0,
                "max_drawdown_pct": 0.0,
                "information_ratio": 0.0,
                "turnover_approx": 0.0,
            }

        total_ret = equity.iloc[-1] / equity.iloc[0] - 1.0
        years = (equity.index[-1] - equity.index[0]).days / 365.25
        ann_ret = (1.0 + total_ret) ** (1.0 / max(years, 1e-6)) - 1.0 if years > 0 else 0.0

        running_max = equity.expanding().max()
        drawdown = (running_max - equity) / running_max
        max_dd = drawdown.max()

        excess = weekly_returns - 0.03 / 52  # rough risk-free
        sharpe = 0.0
        if excess.std() > 0:
            sharpe = (excess.mean() / excess.std()) * math.sqrt(52)

        # Information ratio vs equal-weight universe benchmark.
        common = weekly_returns.index.intersection(benchmark_returns.index)
        ir = 0.0
        if len(common) > 1:
            active = weekly_returns.loc[common]
            passive = benchmark_returns.loc[common]
            tracking_err = (active - passive).std()
            if tracking_err > 0:
                ir = (active - passive).mean() / tracking_err * math.sqrt(52)

        # Approximate turnover from absolute weekly return changes (crude).
        turnover_approx = weekly_returns.diff().abs().mean() * 52.0

        return {
            "annual_return_pct": round(ann_ret * 100, 2),
            "sharpe": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "information_ratio": round(ir, 2),
            "turnover_approx": round(turnover_approx, 2),
        }
