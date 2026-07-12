"""Adaptive backtest engine that uses StockProfile + BandParams per stock.

Compares fixed parameters vs adaptive parameters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from src.ashare.strategies.adaptive_risk import BandParams
from src.ashare.strategies.local_loader import LocalKlineLoader
from src.ashare.strategies.stock_profile import StockProfile
from src.ashare.strategies.trend_timing import Position, Signal, TradeSignal

logger = logging.getLogger(__name__)


@dataclass
class AdaptiveBacktestResult:
    """Results of adaptive backtest."""

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

    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    stock_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"Adaptive Backtest {self.start_date} ~ {self.end_date}\n"
            f"  Initial: {self.initial_cash:,.0f}  Final: {self.final_value:,.0f}\n"
            f"  Total Return: {self.total_return_pct:.1f}%\n"
            f"  Annualized: {self.annualized_return_pct:.1f}%\n"
            f"  Max Drawdown: {self.max_drawdown_pct:.1f}%\n"
            f"  Sharpe: {self.sharpe_ratio:.2f}\n"
            f"  Win Rate: {self.win_rate:.1f}% ({self.num_winning_trades}/{self.num_trades})\n"
            f"  Profit Factor: {self.profit_factor:.2f}\n"
            f"  Avg Holding: {self.avg_holding_days:.1f} days"
        )


class AdaptiveBacktest:
    """Backtest with per-stock adaptive parameters.

    Each stock gets its own BandParams based on its StockProfile.
    """

    COMMISSION_RATE = 0.0003
    SLIPPAGE = 0.001

    def __init__(self, data_root: str | None = None) -> None:
        self.loader = LocalKlineLoader(data_root)
        self._data_cache: dict[str, pd.DataFrame] = {}
        self._profile_cache: dict[str, StockProfile] = {}
        self._params_cache: dict[str, BandParams] = {}
        self._trading_days: list[date] = []

    def preload_data(
        self,
        start_date: date,
        end_date: date,
        universe: list[str] | None = None,
    ) -> None:
        """Pre-load all data and compute profiles."""
        if universe is None:
            universe = self._default_universe()

        begin = (start_date - timedelta(days=180)).strftime("%Y%m%d")
        end = end_date.strftime("%Y%m%d")

        logger.info("preload: %d symbols, %s ~ %s", len(universe), begin, end)
        self._data_cache = self.loader.load_batch(universe, begin, end)
        logger.info("preload: loaded %d symbols", len(self._data_cache))

        # Compute profiles for each stock
        for symbol, df in self._data_cache.items():
            try:
                profile = StockProfile.from_bars(df, symbol=symbol)
                params = BandParams.from_profile(profile)
                self._profile_cache[symbol] = profile
                self._params_cache[symbol] = params
            except Exception as exc:
                logger.debug("profile failed for %s: %s", symbol, exc)

        logger.info("preload: computed %d profiles", len(self._profile_cache))
        self._trading_days = self._generate_trading_days(start_date, end_date)

    def run(
        self,
        start_date: date,
        end_date: date,
        initial_cash: float = 1_000_000.0,
        top_n: int = 20,
        max_positions: int = 10,
    ) -> AdaptiveBacktestResult:
        """Run adaptive backtest."""
        if not self._data_cache:
            self.preload_data(start_date, end_date)

        cash = initial_cash
        positions: dict[str, Position] = {}
        trades_log: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []

        peak_value = initial_cash
        max_drawdown = 0.0

        for td in self._trading_days:
            if td < start_date or td > end_date:
                continue

            try:
                # 1. Select stocks with adaptive filtering
                pool = self._select_stocks(td, top_n)
                if not pool:
                    continue

                # 2. Generate signals with adaptive parameters
                portfolio_value = self._compute_portfolio_value(cash, positions, td)
                signals = self._generate_signals(td, pool, positions, portfolio_value)

                # 3. Execute signals
                for sig in signals:
                    if sig.signal == Signal.SELL and sig.symbol in positions:
                        pos = positions[sig.symbol]
                        sell_price = sig.price * (1 - self.SLIPPAGE)
                        proceeds = sell_price * pos.quantity
                        commission = proceeds * self.COMMISSION_RATE
                        cash += proceeds - commission

                        pnl = (sell_price / pos.entry_price - 1) * 100
                        trades_log.append({
                            "date": td.isoformat(),
                            "symbol": sig.symbol,
                            "action": "sell",
                            "price": sell_price,
                            "quantity": pos.quantity,
                            "pnl_pct": pnl,
                            "reason": sig.reason,
                            "days_held": pos.days_held,
                        })
                        del positions[sig.symbol]

                    elif sig.signal == Signal.BUY and len(positions) < max_positions:
                        params = self._params_cache.get(sig.symbol)
                        if not params:
                            continue

                        buy_price = sig.price * (1 + self.SLIPPAGE)

                        # Use adaptive stop loss and take profit
                        atr = sig.atr if hasattr(sig, 'atr') else buy_price * 0.02
                        stop_loss = params.compute_stop_loss(buy_price, atr)
                        take_profit = params.compute_take_profit(buy_price, atr)

                        # Adaptive position sizing
                        quantity = params.compute_position_size(
                            portfolio_value, buy_price, stop_loss
                        )

                        cost = buy_price * quantity
                        commission = cost * self.COMMISSION_RATE
                        total_cost = cost + commission

                        if total_cost <= cash and quantity > 0:
                            cash -= total_cost
                            positions[sig.symbol] = Position(
                                symbol=sig.symbol,
                                entry_date=td,
                                entry_price=buy_price,
                                quantity=quantity,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                            )
                            trades_log.append({
                                "date": td.isoformat(),
                                "symbol": sig.symbol,
                                "action": "buy",
                                "price": buy_price,
                                "quantity": quantity,
                                "cost": total_cost,
                                "reason": sig.reason,
                                "stop_loss": stop_loss,
                                "take_profit": take_profit,
                            })

                # Update positions
                for pos in positions.values():
                    pos.days_held += 1

                    # Check force exit based on adaptive holding period
                    params = self._params_cache.get(pos.symbol)
                    if params and params.should_force_exit(pos.days_held, pos.unrealized_pnl_pct):
                        # Will be sold next day
                        pass

                # Record equity
                portfolio_value = self._compute_portfolio_value(cash, positions, td)
                if portfolio_value > peak_value:
                    peak_value = portfolio_value
                dd = (peak_value - portfolio_value) / peak_value * 100
                if dd > max_drawdown:
                    max_drawdown = dd

                equity_curve.append({
                    "date": td.isoformat(),
                    "cash": cash,
                    "market_value": portfolio_value - cash,
                    "total_value": portfolio_value,
                    "drawdown_pct": dd,
                    "num_positions": len(positions),
                })

            except Exception as exc:
                logger.warning("backtest day %s failed: %s", td, exc)
                continue

        # Compute metrics
        final_value = equity_curve[-1]["total_value"] if equity_curve else initial_cash
        total_return = (final_value / initial_cash - 1) * 100
        days = (end_date - start_date).days
        years = days / 365.25
        annualized = ((final_value / initial_cash) ** (1 / years) - 1) * 100 if years > 0 else 0

        if len(equity_curve) > 10:
            values = np.array([e["total_value"] for e in equity_curve])
            daily_returns = np.diff(values) / values[:-1]
            excess = daily_returns - 0.03 / 252
            sharpe = np.mean(excess) / np.std(daily_returns) * np.sqrt(252) if np.std(daily_returns) > 0 else 0
        else:
            sharpe = 0

        winning = [t for t in trades_log if t.get("pnl_pct", 0) > 0]
        losing = [t for t in trades_log if t.get("pnl_pct", 0) <= 0 and "pnl_pct" in t]
        win_rate = len(winning) / len(trades_log) * 100 if trades_log else 0
        gross_profit = sum(t["pnl_pct"] for t in winning)
        gross_loss = abs(sum(t["pnl_pct"] for t in losing))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        avg_hold = float(np.mean([t["days_held"] for t in trades_log if "days_held" in t]) if trades_log else 0.0)

        # Collect profiles
        profiles = {
            sym: prof.to_dict()
            for sym, prof in self._profile_cache.items()
        }

        return AdaptiveBacktestResult(
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
            num_winning_trades=len(winning),
            num_losing_trades=len(losing),
            avg_holding_days=avg_hold,
            equity_curve=equity_curve,
            trades=trades_log,
            stock_profiles=profiles,
        )

    def _select_stocks(self, trade_date: date, top_n: int) -> list[dict[str, Any]]:
        """Select stocks using adaptive criteria."""
        scores = []

        for symbol, df in self._data_cache.items():
            td_str = trade_date.strftime("%Y-%m-%d")
            mask = df.index <= td_str
            hist = df[mask]
            if len(hist) < 60:
                continue

            params = self._params_cache.get(symbol)
            if not params:
                continue

            # Compute basic metrics
            ma5 = hist["close"].iloc[-5:].mean()
            ma20 = hist["close"].iloc[-20:].mean()
            ma60 = hist["close"].iloc[-60:].mean()
            momentum_20d = (hist["close"].iloc[-1] / hist["close"].iloc[-20] - 1) * 100
            volume_ratio = hist["volume"].iloc[-1] / hist["volume"].iloc[-20:].mean()

            # Apply adaptive filters
            passes_trend = ma5 > ma20 > ma60 if params.require_trend_alignment else ma5 > ma20
            passes_momentum = momentum_20d > params.min_momentum_pct
            passes_volume = volume_ratio >= params.min_volume_ratio

            if passes_trend and passes_momentum and passes_volume:
                # Score by momentum + volume
                score = momentum_20d * volume_ratio
                scores.append({
                    "symbol": symbol,
                    "score": score,
                    "ma5": ma5,
                    "momentum_20d": momentum_20d,
                    "volume_ratio": volume_ratio,
                })

        scores.sort(key=lambda x: x["score"], reverse=True)
        return scores[:top_n]

    def _generate_signals(
        self,
        trade_date: date,
        stock_pool: list[dict[str, Any]],
        positions: dict[str, Position],
        portfolio_value: float,
    ) -> list[TradeSignal]:
        """Generate signals with adaptive parameters."""
        signals = []
        held_symbols = set(positions.keys())

        # Sell checks
        for symbol, pos in positions.items():
            df = self._data_cache.get(symbol)
            if df is None or len(df) < 5:
                continue
            mask = df.index <= trade_date.strftime("%Y-%m-%d")
            hist = df[mask]
            if len(hist) < 5:
                continue

            current_price = hist["close"].iloc[-1]
            pos.current_price = current_price
            pos.unrealized_pnl_pct = (current_price / pos.entry_price - 1) * 100

            params = self._params_cache.get(symbol)

            # Stop loss
            if current_price <= pos.stop_loss:
                signals.append(TradeSignal(
                    symbol=symbol, signal=Signal.SELL, date=trade_date,
                    price=current_price, reason=f"stop_loss ({pos.unrealized_pnl_pct:.1f}%)"
                ))
            # Take profit
            elif current_price >= pos.take_profit:
                signals.append(TradeSignal(
                    symbol=symbol, signal=Signal.SELL, date=trade_date,
                    price=current_price, reason=f"take_profit ({pos.unrealized_pnl_pct:.1f}%)"
                ))
            # Trend reversal or force exit
            elif pos.days_held > 5:
                ma5 = hist["close"].iloc[-5:].mean()
                ma20 = hist["close"].iloc[-20:].mean()
                if ma5 < ma20:
                    signals.append(TradeSignal(
                        symbol=symbol, signal=Signal.SELL, date=trade_date,
                        price=current_price, reason="trend_reversal"
                    ))
                elif params and pos.days_held >= params.max_holding_days:
                    signals.append(TradeSignal(
                        symbol=symbol, signal=Signal.SELL, date=trade_date,
                        price=current_price, reason=f"max_hold ({params.max_holding_days}d)"
                    ))

        # Buy checks
        for stock in stock_pool:
            symbol = stock["symbol"]
            if symbol in held_symbols:
                continue

            params = self._params_cache.get(symbol)
            if not params:
                continue

            # Already filtered in selection, but double-check
            if stock["momentum_20d"] < params.min_momentum_pct:
                continue
            if stock["volume_ratio"] < params.min_volume_ratio:
                continue

            signals.append(TradeSignal(
                symbol=symbol, signal=Signal.BUY, date=trade_date,
                price=stock["ma5"], reason=f"momentum={stock['momentum_20d']:.1f}%",
            ))

        return signals

    def _compute_portfolio_value(
        self, cash: float, positions: dict[str, Position], td: date
    ) -> float:
        """Compute total portfolio value."""
        market_value = 0.0
        for symbol, pos in positions.items():
            df = self._data_cache.get(symbol)
            if df is not None:
                mask = df.index <= td.strftime("%Y-%m-%d")
                hist = df[mask]
                if len(hist) > 0:
                    price = hist["close"].iloc[-1]
                    market_value += price * pos.quantity
                else:
                    market_value += pos.entry_price * pos.quantity
            else:
                market_value += pos.entry_price * pos.quantity
        return cash + market_value

    def _generate_trading_days(self, start: date, end: date) -> list[date]:
        """Generate trading days."""
        days = []
        d = start
        holidays = {
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

    def _default_universe(self) -> list[str]:
        """Default universe."""
        return [
            "000001.SZ", "000002.SZ", "000063.SZ", "000100.SZ", "000333.SZ",
            "000538.SZ", "000568.SZ", "000651.SZ", "000725.SZ", "000768.SZ",
            "000858.SZ", "000895.SZ", "002001.SZ", "002007.SZ", "002024.SZ",
            "002027.SZ", "002142.SZ", "002230.SZ", "002236.SZ", "002415.SZ",
            "002460.SZ", "002475.SZ", "002594.SZ", "002714.SZ", "300014.SZ",
            "300015.SZ", "300033.SZ", "300059.SZ", "300122.SZ", "300124.SZ",
            "300274.SZ", "300408.SZ", "300433.SZ", "300750.SZ", "600000.SH",
            "600009.SH", "600016.SH", "600028.SH", "600030.SH", "600031.SH",
            "600036.SH", "600048.SH", "600104.SH", "600196.SH", "600276.SH",
            "600309.SH", "600406.SH", "600436.SH", "600519.SH", "600585.SH",
            "600690.SH", "600703.SH", "600745.SH", "600809.SH", "600837.SH",
            "600887.SH", "600900.SH", "601012.SH", "601066.SH", "601088.SH",
            "601166.SH", "601211.SH", "601318.SH", "601336.SH", "601398.SH",
            "601601.SH", "601628.SH", "601668.SH", "601688.SH", "601766.SH",
            "601857.SH", "601888.SH", "601899.SH", "601919.SH", "601995.SH",
            "603259.SH", "603288.SH", "603501.SH", "603986.SH", "605117.SH",
            "688111.SH", "688981.SH",
        ]
