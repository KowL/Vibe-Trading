"""Backtest engine for multi-factor + trend strategy.

Event-driven backtest with realistic execution assumptions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from src.ashare.adshare_client import AdshareClient
from src.ashare.strategies.multi_factor import MultiFactorSelector, StockScore
from src.ashare.strategies.trend_timing import Position, Signal, TrendTiming, TradeSignal

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Results of a backtest run."""

    start_date: date
    end_date: date
    initial_cash: float
    final_value: float
    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    win_rate: float
    profit_factor: float
    num_trades: int
    num_winning_trades: int
    num_losing_trades: int
    avg_holding_days: float

    # Daily series
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    daily_signals: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Backtest {self.start_date} ~ {self.end_date}\n"
            f"  Initial: {self.initial_cash:,.0f}  Final: {self.final_value:,.0f}\n"
            f"  Total Return: {self.total_return_pct:.1f}%\n"
            f"  Annualized: {self.annualized_return_pct:.1f}%\n"
            f"  Max Drawdown: {self.max_drawdown_pct:.1f}%\n"
            f"  Sharpe: {self.sharpe_ratio:.2f}\n"
            f"  Win Rate: {self.win_rate:.1f}% ({self.num_winning_trades}/{self.num_trades})\n"
            f"  Profit Factor: {self.profit_factor:.2f}\n"
            f"  Avg Holding: {self.avg_holding_days:.1f} days"
        )


class MultiFactorBacktest:
    """Event-driven backtest for multi-factor strategy.

    Usage:
        bt = MultiFactorBacktest()
        result = bt.run(
            start_date=date(2022, 1, 1),
            end_date=date(2024, 12, 31),
            initial_cash=1_000_000,
            rebalance_freq="weekly",  # or "daily", "monthly"
        )
        print(result.summary())
    """

    COMMISSION_RATE = 0.0003  # 0.03% per trade (one side)
    SLIPPAGE = 0.001  # 0.1% slippage

    def __init__(self, client: AdshareClient | None = None) -> None:
        self.client = client or AdshareClient()
        self.selector = MultiFactorSelector(client=self.client)
        self.timing = TrendTiming(client=self.client)

    def run(
        self,
        start_date: date,
        end_date: date,
        initial_cash: float = 1_000_000.0,
        rebalance_freq: str = "weekly",
        top_n: int = 20,
        max_positions: int = 10,
    ) -> BacktestResult:
        """Run full backtest."""
        logger.info(
            "backtest: %s ~ %s, cash=%.0f, freq=%s",
            start_date,
            end_date,
            initial_cash,
            rebalance_freq,
        )

        # Generate trading days
        trading_days = self._generate_trading_days(start_date, end_date)
        if len(trading_days) < 30:
            raise ValueError(f"Need at least 30 trading days, got {len(trading_days)}")

        # State
        cash = initial_cash
        positions: dict[str, Position] = {}
        trades_log: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []
        daily_signals_log: list[dict[str, Any]] = []

        peak_value = initial_cash
        max_drawdown = 0.0

        for i, td in enumerate(trading_days):
            # Skip if no market data
            try:
                # 1. Select stocks
                pool = self.selector.select(trade_date=td, top_n=top_n)
                if not pool:
                    continue

                # 2. Generate signals
                current_positions = list(positions.values())
                portfolio_value = self._compute_portfolio_value(cash, positions, td)

                signals = self.timing.generate_signals(
                    trade_date=td,
                    stock_pool=pool,
                    current_positions=current_positions,
                    portfolio_value=portfolio_value,
                )

                # Log signals
                for sig in signals:
                    daily_signals_log.append(
                        {
                            "date": td.isoformat(),
                            "symbol": sig.symbol,
                            "signal": sig.signal.value,
                            "price": sig.price,
                            "reason": sig.reason,
                        }
                    )

                # 3. Execute signals
                for sig in signals:
                    if sig.signal == Signal.SELL and sig.symbol in positions:
                        pos = positions[sig.symbol]
                        # Sell at signal price with slippage
                        sell_price = sig.price * (1 - self.SLIPPAGE)
                        proceeds = sell_price * pos.quantity
                        commission = proceeds * self.COMMISSION_RATE
                        cash += proceeds - commission

                        pnl = (sell_price / pos.entry_price - 1) * 100
                        trades_log.append(
                            {
                                "date": td.isoformat(),
                                "symbol": sig.symbol,
                                "action": "sell",
                                "price": sell_price,
                                "quantity": pos.quantity,
                                "pnl_pct": pnl,
                                "reason": sig.reason,
                                "days_held": pos.days_held,
                            }
                        )
                        del positions[sig.symbol]

                    elif sig.signal == Signal.BUY and len(positions) < max_positions:
                        # Check if we have enough cash
                        buy_price = sig.price * (1 + self.SLIPPAGE)
                        # Risk-based position sizing (simplified)
                        risk_per_trade = portfolio_value * 0.02
                        price_risk = buy_price - (buy_price * 0.92)  # ~8% stop
                        if price_risk <= 0:
                            continue
                        max_shares = int(risk_per_trade / price_risk)
                        max_value = portfolio_value * 0.20  # max 20% per position
                        max_shares_by_value = int(max_value / buy_price)
                        quantity = min(max_shares, max_shares_by_value)

                        cost = buy_price * quantity
                        commission = cost * self.COMMISSION_RATE
                        total_cost = cost + commission

                        if total_cost > cash:
                            # Reduce quantity to fit cash
                            quantity = int(cash / (buy_price * (1 + self.COMMISSION_RATE)))
                            if quantity <= 0:
                                continue
                            cost = buy_price * quantity
                            commission = cost * self.COMMISSION_RATE
                            total_cost = cost + commission

                        if total_cost <= cash and quantity > 0:
                            cash -= total_cost
                            stop_loss = buy_price * 0.92  # 8% stop
                            take_profit = buy_price * 1.15  # 15% profit
                            positions[sig.symbol] = Position(
                                symbol=sig.symbol,
                                entry_date=td,
                                entry_price=buy_price,
                                quantity=quantity,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                            )
                            trades_log.append(
                                {
                                    "date": td.isoformat(),
                                    "symbol": sig.symbol,
                                    "action": "buy",
                                    "price": buy_price,
                                    "quantity": quantity,
                                    "cost": total_cost,
                                    "reason": sig.reason,
                                }
                            )

                # 4. Update position days_held
                for pos in positions.values():
                    pos.days_held += 1

                # 5. Record equity
                portfolio_value = self._compute_portfolio_value(cash, positions, td)
                if portfolio_value > peak_value:
                    peak_value = portfolio_value
                dd = (peak_value - portfolio_value) / peak_value * 100
                if dd > max_drawdown:
                    max_drawdown = dd

                equity_curve.append(
                    {
                        "date": td.isoformat(),
                        "cash": cash,
                        "market_value": portfolio_value - cash,
                        "total_value": portfolio_value,
                        "drawdown_pct": dd,
                        "num_positions": len(positions),
                    }
                )

            except Exception as exc:
                logger.warning("backtest day %s failed: %s", td, exc)
                continue

        # Compute metrics
        final_value = equity_curve[-1]["total_value"] if equity_curve else initial_cash
        total_return = (final_value / initial_cash - 1) * 100

        days = (end_date - start_date).days
        years = days / 365.25
        annualized = ((final_value / initial_cash) ** (1 / years) - 1) * 100 if years > 0 else 0

        # Sharpe ratio (simplified, assuming 3% risk-free rate)
        if len(equity_curve) > 10:
            values = np.array([e["total_value"] for e in equity_curve])
            daily_returns = np.diff(values) / values[:-1]
            excess_returns = daily_returns - 0.03 / 252  # daily risk-free
            sharpe = (
                np.mean(excess_returns) / np.std(daily_returns) * np.sqrt(252)
                if np.std(daily_returns) > 0
                else 0
            )
        else:
            sharpe = 0

        # Win rate
        winning_trades = [t for t in trades_log if t.get("pnl_pct", 0) > 0]
        losing_trades = [t for t in trades_log if t.get("pnl_pct", 0) <= 0]
        win_rate = (
            len(winning_trades) / len(trades_log) * 100 if trades_log else 0
        )

        # Profit factor
        gross_profit = sum(t["pnl_pct"] for t in winning_trades)
        gross_loss = abs(sum(t["pnl_pct"] for t in losing_trades))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # Avg holding days
        avg_hold = float(
            np.mean([t["days_held"] for t in trades_log if "days_held" in t])
            if trades_log
            else 0.0
        )

        return BacktestResult(
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            final_value=final_value,
            total_return_pct=total_return,
            annualized_return_pct=annualized,
            max_drawdown_pct=max_drawdown,
            sharpe_ratio=sharpe,
            win_rate=win_rate,
            profit_factor=profit_factor,
            num_trades=len(trades_log),
            num_winning_trades=len(winning_trades),
            num_losing_trades=len(losing_trades),
            avg_holding_days=avg_hold,
            equity_curve=equity_curve,
            trades=trades_log,
            daily_signals=daily_signals_log,
        )

    def _generate_trading_days(self, start: date, end: date) -> list[date]:
        """Generate trading days (Mon-Fri, excluding simple holiday list)."""
        days = []
        d = start
        # Simple Chinese holidays (2022-2025)
        holidays = {
            date(2022, 1, 1), date(2022, 1, 31), date(2022, 2, 1), date(2022, 2, 2),
            date(2022, 4, 5), date(2022, 5, 1), date(2022, 6, 3), date(2022, 9, 10),
            date(2022, 10, 1), date(2022, 10, 2), date(2022, 10, 3),
            date(2023, 1, 1), date(2023, 1, 23), date(2023, 1, 24), date(2023, 4, 5),
            date(2023, 5, 1), date(2023, 6, 22), date(2023, 9, 29), date(2023, 10, 1),
            date(2023, 10, 2), date(2023, 10, 3),
            date(2024, 1, 1), date(2024, 2, 10), date(2024, 2, 11), date(2024, 2, 12),
            date(2024, 4, 4), date(2024, 5, 1), date(2024, 6, 10), date(2024, 9, 17),
            date(2024, 10, 1), date(2024, 10, 2), date(2024, 10, 3),
            date(2025, 1, 1), date(2025, 1, 29), date(2025, 1, 30), date(2025, 1, 31),
            date(2025, 4, 4), date(2025, 5, 1), date(2025, 5, 31), date(2025, 10, 1),
            date(2025, 10, 2), date(2025, 10, 3),
        }
        while d <= end:
            if d.weekday() < 5 and d not in holidays:
                days.append(d)
            d += timedelta(days=1)
        return days

    def _compute_portfolio_value(
        self, cash: float, positions: dict[str, Position], td: date
    ) -> float:
        """Compute total portfolio value using latest prices."""
        market_value = 0.0
        for symbol, pos in positions.items():
            try:
                df = self.timing._fetch_recent_kline(symbol, td)
                if df is not None and len(df) > 0:
                    price = df["close"].iloc[-1]
                    market_value += price * pos.quantity
                else:
                    market_value += pos.entry_price * pos.quantity  # fallback
            except Exception:
                market_value += pos.entry_price * pos.quantity
        return cash + market_value
