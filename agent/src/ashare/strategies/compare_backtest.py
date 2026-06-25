"""Shared backtest runner used by /backtest and /compare."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from src.ashare.strategies.backtest import _compute_metrics
from src.ashare.strategies.compare_models import (
    LocalSelectParams,
    MultiFactorParams,
    StrategyCompareShared,
    StrategySpec,
)
from src.ashare.strategies.selector_registry import SelectorFn

logger = logging.getLogger(__name__)


@dataclass
class BacktestOutcome:
    """Internal backtest result shared by single and compare routes."""

    name: str
    selector: str
    equity_curve: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    metrics: dict[str, float]


def _price_on_date(panel: dict[str, pd.DataFrame], symbol: str, td: date) -> float | None:
    df = panel.get(symbol)
    if df is None or df.empty:
        return None
    try:
        # Date index may be datetime or date-like.
        row = df.loc[pd.Timestamp(td)]
    except KeyError:
        try:
            row = df.loc[str(td)]
        except KeyError:
            return None
    close = row["close"]
    return float(close) if pd.notna(close) else None


def _available_symbols(panel: dict[str, pd.DataFrame], td: date) -> set[str]:
    symbols: set[str] = set()
    for sym, df in panel.items():
        if df is None or df.empty:
            continue
        try:
            if pd.Timestamp(td) in df.index:
                symbols.add(sym)
        except Exception:
            if str(td) in df.index:
                symbols.add(sym)
    return symbols


def run_backtest(
    *,
    spec: StrategySpec,
    shared: StrategyCompareShared,
    selector_fn: SelectorFn,
    panel: dict[str, pd.DataFrame],
    rebalance_dates: list[date],
) -> BacktestOutcome:
    """Run one backtest for ``spec`` using the shared market settings."""
    params = spec.params_model()
    top_n = params.top_n
    rebalance_every = params.rebalance_days

    # Prepare selector params; pass the panel through a private key so the
    # selector can avoid slow disk/network access when running inside compare.
    selector_params: dict[str, Any] = params.model_dump(exclude={"__selector_kind__"})
    selector_params["_panel"] = panel
    selector_params["_universe"] = list(panel.keys())
    if isinstance(params, MultiFactorParams) and params.factor_weights:
        selector_params["factor_weights"] = params.factor_weights

    cash = shared.initial_cash
    positions: dict[str, tuple[int, float]] = {}  # symbol -> (qty, avg_entry_price)
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []

    commission_rate = shared.commission_bps / 10_000
    slippage_rate = shared.slippage_bps / 10_000

    peak_value = shared.initial_cash

    for i, td in enumerate(rebalance_dates):
        # Mark to market before any action.
        portfolio_value = cash
        for sym, qty in positions.items():
            price = _price_on_date(panel, sym, td)
            if price is not None:
                portfolio_value += price * qty
            else:
                # Hold last known value by keeping the position at cost.
                portfolio_value += 0.0

        is_rebalance = (i % rebalance_every) == 0

        if is_rebalance:
            # Sell all current positions.
            for sym, (qty, entry_price) in list(positions.items()):
                price = _price_on_date(panel, sym, td)
                if price is None:
                    continue
                sell_price = price * (1 - slippage_rate)
                proceeds = sell_price * qty
                commission = proceeds * commission_rate
                cash += proceeds - commission
                pnl_pct = (sell_price / entry_price - 1) * 100 if entry_price > 0 else 0.0
                trades.append(
                    {
                        "date": td.isoformat(),
                        "symbol": sym,
                        "action": "sell",
                        "price": round(sell_price, 4),
                        "quantity": qty,
                        "pnl_pct": round(pnl_pct, 2),
                        "reason": "rebalance",
                    }
                )
                del positions[sym]

            # Select new holdings.
            picks = selector_fn(trade_date=td, top_n=top_n, params=selector_params)
            available = _available_symbols(panel, td)
            picks = [p for p in picks if getattr(p, "symbol", None) in available][:top_n]

            if picks and cash > 0:
                weight_cash = cash / len(picks)
                for pick in picks:
                    sym = getattr(pick, "symbol", None)
                    price = _price_on_date(panel, sym, td)
                    if price is None or sym is None:
                        continue
                    buy_price = price * (1 + slippage_rate)
                    max_qty = int(weight_cash / (buy_price * (1 + commission_rate)))
                    if max_qty <= 0:
                        continue
                    cost = buy_price * max_qty
                    commission = cost * commission_rate
                    total_cost = cost + commission
                    if total_cost > cash:
                        continue
                    cash -= total_cost
                    old_qty, old_entry = positions.get(sym, (0, 0.0))
                    new_qty = old_qty + max_qty
                    new_entry = (
                        (old_qty * old_entry + max_qty * buy_price) / new_qty
                        if new_qty > 0
                        else buy_price
                    )
                    positions[sym] = (new_qty, new_entry)
                    trades.append(
                        {
                            "date": td.isoformat(),
                            "symbol": sym,
                            "action": "buy",
                            "price": round(buy_price, 4),
                            "quantity": max_qty,
                            "cost": round(total_cost, 2),
                            "reason": "rebalance",
                        }
                    )

        # Recompute portfolio value after trading.
        portfolio_value = cash
        for sym, (qty, _) in positions.items():
            price = _price_on_date(panel, sym, td)
            if price is not None:
                portfolio_value += price * qty

        if portfolio_value > peak_value:
            peak_value = portfolio_value
        drawdown_pct = (peak_value - portfolio_value) / peak_value * 100 if peak_value > 0 else 0.0

        equity_curve.append(
            {
                "date": td.isoformat(),
                "cash": round(cash, 2),
                "market_value": round(portfolio_value - cash, 2),
                "total_value": round(portfolio_value, 2),
                "drawdown_pct": round(drawdown_pct, 2),
                "num_positions": len(positions),
            }
        )

    metrics = _compute_metrics(
        equity_curve=equity_curve,
        trades=trades,
        initial_cash=shared.initial_cash,
        start_date=shared.start_date,
        end_date=shared.end_date,
    )

    return BacktestOutcome(
        name=spec.name,
        selector=spec.selector,
        equity_curve=equity_curve,
        trades=trades,
        metrics=metrics,
    )
