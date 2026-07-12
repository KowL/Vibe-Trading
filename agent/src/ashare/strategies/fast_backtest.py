"""Fast backtest engine using local DuckDB/Parquet data.

Replaces HTTP API calls with direct Parquet reads for 100x speedup.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from src.ashare.strategies.local_loader import LocalKlineLoader
from src.ashare.strategies.multi_factor import FactorScore, StockScore
from src.ashare.strategies.trend_timing import Position, Signal, TradeSignal

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


class FastMultiFactorBacktest:
    """Fast event-driven backtest using local Parquet data.

    Usage:
        bt = FastMultiFactorBacktest()
        bt.preload_data(start_date=date(2022,1,1), end_date=date(2024,12,31))
        result = bt.run(start_date=date(2022,1,1), end_date=date(2024,12,31))
        print(result.summary())
    """

    COMMISSION_RATE = 0.0003  # 0.03% per trade
    SLIPPAGE = 0.001  # 0.1%

    # Alive factors
    ALIVE_FACTORS: list[dict[str, Any]] = [
        {"id": "gtja191_120", "weight": 0.30},
        {"id": "gtja191_114", "weight": 0.25},
        {"id": "gtja191_171", "weight": 0.25},
        {"id": "gtja191_111", "weight": 0.20},
    ]

    def __init__(self, data_root: str | None = None) -> None:
        self.loader = LocalKlineLoader(data_root)
        self._data_cache: dict[str, pd.DataFrame] = {}
        self._trading_days: list[date] = []

    def preload_data(
        self,
        start_date: date,
        end_date: date,
        universe: list[str] | None = None,
    ) -> None:
        """Pre-load all K-line data for backtest period."""
        if universe is None:
            universe = self._default_universe()

        begin = (start_date - timedelta(days=180)).strftime("%Y%m%d")
        end = end_date.strftime("%Y%m%d")

        logger.info("preload: %d symbols, %s ~ %s", len(universe), begin, end)
        self._data_cache = self.loader.load_batch(universe, begin, end)
        logger.info("preload: %d symbols loaded", len(self._data_cache))

        # Generate trading days from calendar
        self._trading_days = self._generate_trading_days(start_date, end_date)
        logger.info("preload: %d trading days", len(self._trading_days))

    def run(
        self,
        start_date: date,
        end_date: date,
        initial_cash: float = 1_000_000.0,
        top_n: int = 20,
        max_positions: int = 10,
        min_composite_score: float = 0.5,
    ) -> BacktestResult:
        """Run backtest using pre-loaded data."""
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
                # 1. Select stocks for today
                pool = self._select_stocks(td, top_n, min_composite_score)
                if not pool:
                    continue

                # 2. Generate signals
                current_positions = list(positions.values())
                portfolio_value = self._compute_portfolio_value(cash, positions, td)
                signals = self._generate_signals(td, pool, current_positions, portfolio_value)

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
                        buy_price = sig.price * (1 + self.SLIPPAGE)
                        risk_per_trade = portfolio_value * 0.02
                        price_risk = buy_price - (buy_price * 0.92)
                        if price_risk <= 0:
                            continue
                        max_shares = int(risk_per_trade / price_risk)
                        max_value = portfolio_value * 0.20
                        max_shares_by_value = int(max_value / buy_price)
                        quantity = min(max_shares, max_shares_by_value)

                        cost = buy_price * quantity
                        commission = cost * self.COMMISSION_RATE
                        total_cost = cost + commission

                        if total_cost <= cash and quantity > 0:
                            cash -= total_cost
                            stop_loss = buy_price * 0.92
                            take_profit = buy_price * 1.15
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
                            })

                # 4. Update positions
                for pos in positions.values():
                    pos.days_held += 1

                # 5. Record equity
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

        # Sharpe
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
            num_winning_trades=len(winning),
            num_losing_trades=len(losing),
            avg_holding_days=avg_hold,
            equity_curve=equity_curve,
            trades=trades_log,
        )

    def _select_stocks(
        self, trade_date: date, top_n: int, min_score: float
    ) -> list[StockScore]:
        """Select stocks using pre-loaded data."""
        scores: list[StockScore] = []

        for symbol, df in self._data_cache.items():
            # Filter data up to trade_date
            td_str = trade_date.strftime("%Y-%m-%d")
            mask = df.index <= td_str
            hist = df[mask]
            if len(hist) < 60:
                continue

            # Compute factors
            factor_scores: dict[str, FactorScore] = {}
            for factor in self.ALIVE_FACTORS:
                fid = factor["id"]
                try:
                    val = self._compute_factor(fid, hist)
                    factor_scores[fid] = FactorScore(
                        symbol=symbol, factor_id=fid, value=val, rank=0, percentile=0.0
                    )
                except Exception:
                    continue

            # Compute composite (simplified: equal weight for speed)
            if factor_scores:
                avg_pct = np.mean([fs.percentile for fs in factor_scores.values()]) if False else 0.5
                # For speed, use raw value ranking instead of cross-sectional percentile
                score = StockScore(
                    symbol=symbol,
                    factor_scores=factor_scores,
                    composite_score=0.5,  # placeholder
                )

                # Trend metrics
                score.ma5 = hist["close"].iloc[-5:].mean()
                score.ma20 = hist["close"].iloc[-20:].mean()
                score.ma60 = hist["close"].iloc[-60:].mean()
                score.momentum_20d = (hist["close"].iloc[-1] / hist["close"].iloc[-20] - 1) * 100
                score.volume_ratio = hist["volume"].iloc[-1] / hist["volume"].iloc[-20:].mean()

                # ATR
                high = hist["high"].values
                low = hist["low"].values
                close_arr = hist["close"].values
                tr1 = high - low
                tr2 = np.abs(high - np.roll(close_arr, 1))
                tr3 = np.abs(low - np.roll(close_arr, 1))
                tr = np.maximum(np.maximum(tr1, tr2), tr3)
                score.atr_14 = float(np.mean(tr[-14:]))

                # Filters
                score.passes_trend = score.ma5 > score.ma20 > score.ma60
                score.passes_momentum = score.momentum_20d > 0
                score.passes_volume = score.volume_ratio >= 1.0
                score.passes_all = (
                    score.passes_trend and score.passes_momentum and score.passes_volume
                )

                if score.passes_all:
                    scores.append(score)

        # Cross-sectional ranking for composite score
        if scores:
            for factor in self.ALIVE_FACTORS:
                fid = factor["id"]
                values = [(s, s.factor_scores[fid].value) for s in scores if fid in s.factor_scores]
                values.sort(key=lambda x: x[1], reverse=True)
                total = len(values)
                for rank, (score, val) in enumerate(values, 1):
                    score.factor_scores[fid].percentile = (total - rank + 1) / total

            # Recompute composite
            for score in scores:
                weighted_sum = 0.0
                weight_sum = 0.0
                for factor in self.ALIVE_FACTORS:
                    fid = factor["id"]
                    weight = factor["weight"]
                    fs = score.factor_scores.get(fid)
                    if fs:
                        weighted_sum += fs.percentile * weight
                        weight_sum += weight
                if weight_sum > 0:
                    score.composite_score = weighted_sum / weight_sum

            scores.sort(key=lambda x: x.composite_score, reverse=True)
            for i, s in enumerate(scores, 1):
                s.composite_rank = i

        return [s for s in scores[:top_n] if s.composite_score >= min_score]

    def _generate_signals(
        self,
        trade_date: date,
        stock_pool: list[StockScore],
        current_positions: list[Position],
        portfolio_value: float,
    ) -> list[TradeSignal]:
        """Generate buy/sell signals."""
        signals: list[TradeSignal] = []
        held_symbols = {p.symbol for p in current_positions}

        # Sell checks
        for pos in current_positions:
            df = self._data_cache.get(pos.symbol)
            if df is None or len(df) < 5:
                continue
            mask = df.index <= trade_date.strftime("%Y-%m-%d")
            hist = df[mask]
            if len(hist) < 5:
                continue

            current_price = hist["close"].iloc[-1]
            pos.current_price = current_price
            pos.unrealized_pnl_pct = (current_price / pos.entry_price - 1) * 100

            if current_price <= pos.stop_loss:
                signals.append(TradeSignal(
                    symbol=pos.symbol, signal=Signal.SELL, date=trade_date,
                    price=current_price, reason=f"stop_loss ({pos.unrealized_pnl_pct:.1f}%)"
                ))
            elif current_price >= pos.take_profit:
                signals.append(TradeSignal(
                    symbol=pos.symbol, signal=Signal.SELL, date=trade_date,
                    price=current_price, reason=f"take_profit ({pos.unrealized_pnl_pct:.1f}%)"
                ))
            else:
                ma5 = hist["close"].iloc[-5:].mean()
                ma20 = hist["close"].iloc[-20:].mean()
                if ma5 < ma20 and pos.days_held > 5:
                    signals.append(TradeSignal(
                        symbol=pos.symbol, signal=Signal.SELL, date=trade_date,
                        price=current_price, reason=f"trend_reversal"
                    ))

        # Buy checks
        for score in stock_pool:
            if score.symbol in held_symbols:
                continue
            if score.composite_score < 0.5:
                continue
            if score.momentum_20d < 2.0:
                continue
            if score.volume_ratio < 1.2:
                continue

            atr_pct = (score.atr_14 / score.ma5) * 100 if score.ma5 > 0 else 5.0
            sl_mult = 1.5 if atr_pct < 5.0 else 2.5
            tp_mult = 3.0 if atr_pct < 5.0 else 4.0

            stop_loss = score.ma5 - score.atr_14 * sl_mult
            take_profit = score.ma5 + score.atr_14 * tp_mult

            risk_per_trade = portfolio_value * 0.02
            price_risk = score.ma5 - stop_loss
            if price_risk <= 0:
                continue
            max_shares = int(risk_per_trade / price_risk)
            max_value = portfolio_value * 0.20
            max_shares_by_value = int(max_value / score.ma5)
            quantity = min(max_shares, max_shares_by_value)

            if quantity > 0:
                signals.append(TradeSignal(
                    symbol=score.symbol, signal=Signal.BUY, date=trade_date,
                    price=score.ma5, reason=f"composite={score.composite_score:.2f}",
                    ma5=score.ma5, ma20=score.ma20, ma60=score.ma60,
                    momentum_20d=score.momentum_20d, volume_ratio=score.volume_ratio,
                    composite_score=score.composite_score,
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

    def _compute_factor(self, factor_id: str, df: pd.DataFrame) -> float:
        """Compute factor value."""
        close = df["close"]
        volume = df["volume"]
        amount = df["amount"]
        vwap = amount / (volume + 1e-9)

        if factor_id == "gtja191_120":
            return (vwap.iloc[-1] - close.iloc[-1]) / (vwap.iloc[-1] + close.iloc[-1])
        elif factor_id == "gtja191_114":
            ret = close.pct_change().iloc[-20:]
            vol = volume.iloc[-20:]
            if len(ret) < 10:
                return 0.0
            return np.corrcoef(ret.fillna(0), vol.fillna(0))[0, 1]
        elif factor_id == "gtja191_171":
            ret = close.pct_change().iloc[-10:]
            if len(ret) < 3:
                return 0.0
            return ret.diff().iloc[-1] * 100
        elif factor_id == "gtja191_111":
            vol_ratio = volume.iloc[-1] / volume.iloc[-20:].mean()
            ret = close.pct_change().iloc[-5:]
            price_accel = ret.diff().iloc[-1] if len(ret) >= 2 else 0
            return vol_ratio * price_accel * 10
        return 0.0

    def _generate_trading_days(self, start: date, end: date) -> list[date]:
        """Generate trading days (Mon-Fri, excluding holidays)."""
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

    def _default_universe(self) -> list[str]:
        """Default liquid universe."""
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
