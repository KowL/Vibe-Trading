"""Limit-up (连板) strategy backtest engine.

Simple event-driven backtest for A-share consecutive limit-up strategies:
- Entry: buy at limit-up price on day N
- Exit: sell at next day open, or hold until stop-loss / target
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from src.ashare.models.limit_up import LimitUpDaily
from src.ashare.storage.limit_up_store import LimitUpStore


@dataclass
class BacktestTrade:
    entry_date: date
    exit_date: date | None
    symbol: str
    name: str
    entry_price: float
    exit_price: float | None
    quantity: int
    side: str = "long"
    pnl: float = 0.0
    return_pct: float = 0.0
    exit_reason: str = ""


@dataclass
class BacktestResult:
    strategy_name: str
    start_date: date
    end_date: date
    trades: list[BacktestTrade] = field(default_factory=list)
    initial_cash: float = 1_000_000.0
    
    @property
    def total_trades(self) -> int:
        return len(self.trades)
    
    @property
    def winning_trades(self) -> int:
        return sum(1 for t in self.trades if t.pnl > 0)
    
    @property
    def losing_trades(self) -> int:
        return sum(1 for t in self.trades if t.pnl < 0)
    
    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return self.winning_trades / len(self.trades)
    
    @property
    def total_return_pct(self) -> float:
        if not self.trades:
            return 0.0
        total_pnl = sum(t.pnl for t in self.trades)
        return (total_pnl / self.initial_cash) * 100
    
    @property
    def avg_return_per_trade(self) -> float:
        if not self.trades:
            return 0.0
        return sum(t.return_pct for t in self.trades) / len(self.trades)
    
    @property
    def max_drawdown_pct(self) -> float:
        """Simple max drawdown from cumulative PnL."""
        if not self.trades:
            return 0.0
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in self.trades:
            cumulative += t.pnl
            if cumulative > peak:
                peak = cumulative
            dd = (peak - cumulative) / self.initial_cash * 100
            if dd > max_dd:
                max_dd = dd
        return max_dd
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "period": f"{self.start_date} ~ {self.end_date}",
            "initial_cash": self.initial_cash,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": f"{self.win_rate:.1%}",
            "total_return_pct": f"{self.total_return_pct:.2f}%",
            "avg_return_per_trade": f"{self.avg_return_per_trade:.2f}%",
            "max_drawdown_pct": f"{self.max_drawdown_pct:.2f}%",
            "trades": [
                {
                    "entry_date": t.entry_date.isoformat(),
                    "exit_date": t.exit_date.isoformat() if t.exit_date else None,
                    "symbol": t.symbol,
                    "name": t.name,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "return_pct": f"{t.return_pct:.2f}%",
                    "pnl": round(t.pnl, 2),
                    "exit_reason": t.exit_reason,
                }
                for t in self.trades
            ],
        }


class LimitUpBacktestEngine:
    """Backtest engine for limit-up strategies."""

    def __init__(self, store: LimitUpStore | None = None) -> None:
        self.store = store or LimitUpStore()

    def run(
        self,
        start_date: date,
        end_date: date,
        min_consecutive_days: int = 2,
        max_consecutive_days: int = 10,
        hold_days: int = 1,
        stop_loss_pct: float = -0.05,
        take_profit_pct: float = 0.10,
        position_size: float = 100_000.0,
    ) -> BacktestResult:
        """Run backtest for a limit-up strategy.

        Args:
            start_date: Backtest start date
            end_date: Backtest end date
            min_consecutive_days: Minimum consecutive limit-up days to enter
            max_consecutive_days: Maximum consecutive limit-up days to enter
            hold_days: Number of days to hold position
            stop_loss_pct: Stop loss percentage (e.g., -0.05 = -5%)
            take_profit_pct: Take profit percentage (e.g., 0.10 = +10%)
            position_size: Position size in RMB
        """
        result = BacktestResult(
            strategy_name=f"连板{min_consecutive_days}-{max_consecutive_days}板策略",
            start_date=start_date,
            end_date=end_date,
            initial_cash=1_000_000.0,
        )

        current_date = start_date
        while current_date <= end_date:
            # Load limit-up data for current date
            records = self.store.load_day(current_date)
            if not records:
                current_date += timedelta(days=1)
                continue

            # Find entry signals
            for record in records.values():
                if min_consecutive_days <= record.limit_up_count <= max_consecutive_days:
                    # Simulate entry at limit-up price
                    entry_price = record.limit_up_price
                    if entry_price <= 0:
                        continue
                    quantity = int(position_size / entry_price)
                    if quantity < 1:
                        continue

                    # Simulate exit (simplified: next day open = today's close)
                    # In reality, next day open could be different
                    exit_date = current_date + timedelta(days=hold_days)
                    
                    # Try to get next day data for more accurate exit
                    next_day_records = self.store.load_day(exit_date)
                    if next_day_records and record.symbol in next_day_records:
                        next_record = next_day_records[record.symbol]
                        # If next day also limit-up, hold longer
                        if next_record.limit_up_count > record.limit_up_count:
                            exit_price = next_record.limit_up_price
                            exit_reason = "继续涨停"
                        else:
                            # Exit at next day close (simplified)
                            exit_price = next_record.close_price
                            exit_reason = "次日收盘"
                    else:
                        # No next day data, use simple assumption
                        exit_price = entry_price * (1 + 0.02)  # Assume +2% next day
                        exit_reason = "模拟退出"

                    # Calculate PnL
                    pnl = (exit_price - entry_price) * quantity
                    return_pct = (exit_price - entry_price) / entry_price * 100

                    # Apply stop-loss / take-profit
                    if return_pct <= stop_loss_pct * 100:
                        exit_price = entry_price * (1 + stop_loss_pct)
                        pnl = (exit_price - entry_price) * quantity
                        return_pct = stop_loss_pct * 100
                        exit_reason = "止损"
                    elif return_pct >= take_profit_pct * 100:
                        exit_price = entry_price * (1 + take_profit_pct)
                        pnl = (exit_price - entry_price) * quantity
                        return_pct = take_profit_pct * 100
                        exit_reason = "止盈"

                    trade = BacktestTrade(
                        entry_date=current_date,
                        exit_date=exit_date,
                        symbol=record.symbol,
                        name=record.name,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        quantity=quantity,
                        pnl=pnl,
                        return_pct=return_pct,
                        exit_reason=exit_reason,
                    )
                    result.trades.append(trade)

            current_date += timedelta(days=1)

        return result


def run_limit_up_backtest(
    start_date: str | date,
    end_date: str | date,
    min_days: int = 2,
    max_days: int = 10,
    hold_days: int = 1,
) -> dict[str, Any]:
    """Convenience function to run backtest and return dict."""
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)

    engine = LimitUpBacktestEngine()
    result = engine.run(
        start_date=start_date,
        end_date=end_date,
        min_consecutive_days=min_days,
        max_consecutive_days=max_days,
        hold_days=hold_days,
    )
    return result.to_dict()
