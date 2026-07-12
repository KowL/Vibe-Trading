"""Strategy compare engine: parallel dispatch + curve alignment."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

import pandas as pd
from fastapi import HTTPException

from src.ashare.strategies.compare_backtest import BacktestOutcome, run_backtest
from src.ashare.strategies.compare_models import (
    AlignedCurve,
    AlignmentInfo,
    CurvePoint,
    StrategyCompareRequest,
    StrategyCompareResponse,
    StrategyMetrics,
)
from src.ashare.strategies.local_loader import load_panel_cached
from src.ashare.strategies.selector_registry import (
    UnknownSelectorError,
    resolve_selector,
)

logger = logging.getLogger(__name__)


def _rebalance_dates(
    panel: dict[str, pd.DataFrame], start_date: date, end_date: date
) -> list[date]:
    """Return the sorted union of all dates in ``panel`` within the range."""
    dates: set[date] = set()
    for df in panel.values():
        if df is None or df.empty:
            continue
        for d in df.index:
            try:
                dt = d.date() if hasattr(d, "date") else date.fromisoformat(str(d))
            except Exception:
                continue
            if start_date <= dt <= end_date:
                dates.add(dt)
    return sorted(dates)


def _align_curves(
    outcomes: list[BacktestOutcome],
) -> tuple[list[date], list[AlignedCurve], dict[str, int], float]:
    """Intersect equity curves by date and build aligned curve objects."""
    per = [
        {date.fromisoformat(p["date"]): p for p in o.equity_curve}
        for o in outcomes
    ]
    common = set.intersection(*(set(d.keys()) for d in per)) if per else set()
    common_dates = sorted(common)

    aligned = [
        AlignedCurve(
            name=o.name,
            points=[
                CurvePoint(
                    date=d,
                    total_value=per[i][d]["total_value"],
                    drawdown_pct=per[i][d]["drawdown_pct"],
                    num_positions=per[i][d]["num_positions"],
                )
                for d in common_dates
            ],
        )
        for i, o in enumerate(outcomes)
    ]

    dropped = {
        o.name: len(o.equity_curve) - len(aligned[i].points)
        for i, o in enumerate(outcomes)
    }

    avg_len = sum(len(o.equity_curve) for o in outcomes) / len(outcomes) if outcomes else 0.0
    coverage = len(common_dates) / avg_len if avg_len > 0 else 0.0

    return common_dates, aligned, dropped, coverage


def _build_response(
    req: StrategyCompareRequest, outcomes: list[BacktestOutcome]
) -> StrategyCompareResponse:
    common_dates, aligned, dropped, coverage = _align_curves(outcomes)

    metrics: list[StrategyMetrics] = []
    for spec, outcome in zip(req.strategies, outcomes):
        m = outcome.metrics
        final_value = outcome.equity_curve[-1]["total_value"] if outcome.equity_curve else req.shared.initial_cash
        days = (req.shared.end_date - req.shared.start_date).days
        metrics.append(
            StrategyMetrics(
                name=spec.name,
                selector=spec.selector,
                start_date=req.shared.start_date,
                end_date=req.shared.end_date,
                initial_cash=req.shared.initial_cash,
                final_value=round(final_value, 2),
                total_return_pct=m["total_return_pct"],
                annualized_return_pct=m["annualized_return_pct"] if days >= 30 else None,
                max_drawdown_pct=m["max_drawdown_pct"],
                sharpe=m["sharpe"],
                profit_factor=m["profit_factor"],
                num_trades=m["num_trades"],
                avg_holding_days=m["avg_holding_days"],
            )
        )

    warning: Any = "low_coverage" if coverage < 0.7 else None
    alignment = AlignmentInfo(
        common_dates=common_dates,
        per_strategy_dropped=dropped,
        coverage_ratio=round(coverage, 4),
        warning=warning,
    )

    return StrategyCompareResponse(
        shared=req.shared,
        alignment=alignment,
        metrics=metrics,
        curves=aligned,
    )


def run_compare(req: StrategyCompareRequest) -> StrategyCompareResponse:
    """Run all strategy specs in parallel and return aligned results."""
    logger.info(
        "strategy.compare.start universe=%s start=%s end=%s strategies=%d",
        req.shared.universe,
        req.shared.start_date,
        req.shared.end_date,
        len(req.strategies),
    )
    t0 = time.monotonic()

    try:
        panel = load_panel_cached(
            req.shared.universe, req.shared.start_date, req.shared.end_date
        )
    except Exception as exc:
        logger.exception("panel load failed")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "panel_load_failed",
                "universe": req.shared.universe,
                "detail": str(exc),
            },
        ) from exc

    rebars = _rebalance_dates(panel, req.shared.start_date, req.shared.end_date)

    selectors: list[Any] = []
    for spec in req.strategies:
        try:
            selectors.append(resolve_selector(spec.selector))
        except UnknownSelectorError as exc:
            logger.error("unknown selector: %s", exc.name)
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "unknown_selector",
                    "selector": exc.name,
                    "available": list_selectors(),
                },
            ) from exc

    def _one(spec: Any, selector_fn: Any) -> BacktestOutcome:
        return run_backtest(
            spec=spec,
            shared=req.shared,
            selector_fn=selector_fn,
            panel=panel,
            rebalance_dates=rebars,
        )

    try:
        with ThreadPoolExecutor(max_workers=len(req.strategies)) as pool:
            outcomes: list[BacktestOutcome] = list(
                pool.map(lambda args: _one(*args), zip(req.strategies, selectors))
            )
    except Exception as exc:
        logger.exception("spec failed during compare")
        # Try to identify which spec failed from the exception context.
        name = getattr(exc, "name", None)
        selector = getattr(exc, "selector", None)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "spec_failed",
                "name": name or "unknown",
                "selector": selector or "unknown",
                "detail": str(exc),
            },
        ) from exc

    common, aligned, dropped, coverage = _align_curves(outcomes)
    logger.info(
        "strategy.compare.done elapsed_ms=%.1f common_dates=%d coverage=%.3f metrics=%s",
        (time.monotonic() - t0) * 1000,
        len(common),
        coverage,
        [(o.name, o.metrics["num_trades"]) for o in outcomes],
    )

    return _build_response(req, outcomes)


def list_selectors() -> list[str]:
    """Re-export for error payloads."""
    from src.ashare.strategies.selector_registry import list_selectors as _list

    return _list()
