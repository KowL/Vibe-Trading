"""Trend timing signal generator for multi-factor strategy.

Generates buy/sell/hold signals based on:
- Moving average alignment
- Momentum confirmation
- Volume confirmation
- Adaptive stop-loss / take-profit
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from src.ashare.strategies.multi_factor import MultiFactorSelector, StockScore
from src.ashare.tushare_client import TushareClient

logger = logging.getLogger(__name__)


class Signal(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class Position:
    """Current position state."""

    symbol: str
    entry_date: date
    entry_price: float
    quantity: int
    stop_loss: float  # price level
    take_profit: float  # price level
    max_position_pct: float = 0.20  # max 20% of portfolio

    # Runtime state
    current_price: float = 0.0
    unrealized_pnl_pct: float = 0.0
    days_held: int = 0


@dataclass
class TradeSignal:
    """A trade signal with full context."""

    symbol: str
    signal: Signal
    date: date
    price: float
    reason: str

    # Context
    ma5: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    momentum_20d: float = 0.0
    volume_ratio: float = 0.0
    composite_score: float = 0.0


class TrendTiming:
    """Generate trend-following signals with adaptive risk management.

    Usage:
        timing = TrendTiming()
        signals = timing.generate_signals(
            trade_date=date(2025, 6, 10),
            stock_pool=[...],  # from MultiFactorSelector
            current_positions=[...],
        )
    """

    # Entry thresholds
    MIN_COMPOSITE_SCORE = 0.60
    MIN_MOMENTUM_20D = 2.0  # at least 2% gain in 20 days
    MIN_VOLUME_RATIO = 1.2  # 20% above average

    # Exit thresholds (base, will be adapted by volatility)
    BASE_STOP_LOSS_PCT = -8.0  # -8%
    BASE_TAKE_PROFIT_PCT = 15.0  # +15%

    # Volatility adaptation
    LOW_VOL_ATR_MULTIPLIER = 1.5
    HIGH_VOL_ATR_MULTIPLIER = 2.5
    ATR_VOLATILITY_THRESHOLD = 5.0  # ATR as % of price

    def __init__(self, client: TushareClient | None = None) -> None:
        self.client = client or TushareClient()

    def generate_signals(
        self,
        trade_date: date,
        stock_pool: list[StockScore],
        current_positions: list[Position],
        portfolio_value: float = 1000000.0,
    ) -> list[TradeSignal]:
        """Generate buy/sell signals for the day.

        Args:
            trade_date: current trading date
            stock_pool: ranked stocks from MultiFactorSelector
            current_positions: currently held positions
            portfolio_value: total portfolio value for position sizing

        Returns:
            List of trade signals to execute
        """
        signals: list[TradeSignal] = []
        held_symbols = {p.symbol for p in current_positions}

        # --- SELL signals: check existing positions ---
        for pos in current_positions:
            try:
                df = self._fetch_recent_kline(pos.symbol, trade_date)
                if df is None or len(df) < 5:
                    continue
                current_price = df["close"].iloc[-1]
                pos.current_price = current_price
                pos.unrealized_pnl_pct = (current_price / pos.entry_price - 1) * 100
                pos.days_held += 1

                # Stop loss hit
                if current_price <= pos.stop_loss:
                    signals.append(
                        TradeSignal(
                            symbol=pos.symbol,
                            signal=Signal.SELL,
                            date=trade_date,
                            price=current_price,
                            reason=f"stop_loss triggered ({pos.unrealized_pnl_pct:.1f}%)",
                        )
                    )
                    continue

                # Take profit hit
                if current_price >= pos.take_profit:
                    signals.append(
                        TradeSignal(
                            symbol=pos.symbol,
                            signal=Signal.SELL,
                            date=trade_date,
                            price=current_price,
                            reason=f"take_profit triggered ({pos.unrealized_pnl_pct:.1f}%)",
                        )
                    )
                    continue

                # Trend reversal: MA5 crosses below MA20
                ma5 = df["close"].iloc[-5:].mean()
                ma20 = df["close"].iloc[-20:].mean()
                if ma5 < ma20 and pos.days_held > 5:
                    signals.append(
                        TradeSignal(
                            symbol=pos.symbol,
                            signal=Signal.SELL,
                            date=trade_date,
                            price=current_price,
                            reason=f"trend_reversal (MA5 {ma5:.2f} < MA20 {ma20:.2f})",
                        )
                    )
                    continue

            except Exception as exc:
                logger.warning("sell check failed for %s: %s", pos.symbol, exc)

        # --- BUY signals: check stock pool ---
        # Only buy if we have cash and not already holding
        for score in stock_pool:
            if score.symbol in held_symbols:
                continue  # already holding

            # Entry criteria
            if score.composite_score < self.MIN_COMPOSITE_SCORE:
                continue
            if score.momentum_20d < self.MIN_MOMENTUM_20D:
                continue
            if score.volume_ratio < self.MIN_VOLUME_RATIO:
                continue

            # Compute adaptive stop-loss / take-profit based on ATR
            atr_pct = (score.atr_14 / score.ma5) * 100 if score.ma5 > 0 else 5.0
            if atr_pct < self.ATR_VOLATILITY_THRESHOLD:
                # Low volatility: tighter stop, moderate profit
                sl_mult = self.LOW_VOL_ATR_MULTIPLIER
                tp_mult = 3.0
            else:
                # High volatility: wider stop, higher profit target
                sl_mult = self.HIGH_VOL_ATR_MULTIPLIER
                tp_mult = 4.0

            stop_loss = score.ma5 - score.atr_14 * sl_mult
            take_profit = score.ma5 + score.atr_14 * tp_mult

            # Position sizing: risk-based
            risk_per_trade = portfolio_value * 0.02  # 2% risk per trade
            price_risk = score.ma5 - stop_loss
            if price_risk <= 0:
                continue
            max_shares = int(risk_per_trade / price_risk)
            max_position_pct = 0.20  # default 20% max position
            max_value = portfolio_value * max_position_pct
            max_shares_by_value = int(max_value / score.ma5)
            quantity = min(max_shares, max_shares_by_value)

            if quantity <= 0:
                continue

            signals.append(
                TradeSignal(
                    symbol=score.symbol,
                    signal=Signal.BUY,
                    date=trade_date,
                    price=score.ma5,
                    reason=(
                        f"composite={score.composite_score:.2f} "
                        f"momentum={score.momentum_20d:.1f}% "
                        f"vol_ratio={score.volume_ratio:.1f}x"
                    ),
                    ma5=score.ma5,
                    ma20=score.ma20,
                    ma60=score.ma60,
                    momentum_20d=score.momentum_20d,
                    volume_ratio=score.volume_ratio,
                    composite_score=score.composite_score,
                )
            )

        logger.info(
            "generate_signals: %d buy, %d sell, %d hold",
            sum(1 for s in signals if s.signal == Signal.BUY),
            sum(1 for s in signals if s.signal == Signal.SELL),
            len(held_symbols) - sum(1 for s in signals if s.signal == Signal.SELL),
        )
        return signals

    def _fetch_recent_kline(self, symbol: str, trade_date: date) -> pd.DataFrame | None:
        """Fetch last 60 days of K-line."""
        from datetime import timedelta
        begin = (trade_date - timedelta(days=90)).strftime("%Y%m%d")
        end = trade_date.strftime("%Y%m%d")
        try:
            return self.client.get_kline(symbol, period="daily", begin_date=begin, end_date=end)
        except Exception as exc:
            logger.debug("fetch kline failed for %s: %s", symbol, exc)
            return None


def run_strategy_day(
    trade_date: date,
    portfolio_value: float = 1000000.0,
    top_n: int = 20,
) -> list[TradeSignal]:
    """Convenience function: run full pipeline for one day.

    Returns:
        List of trade signals
    """
    # 1. Select stocks
    selector = MultiFactorSelector()
    pool = selector.select(trade_date=trade_date, top_n=top_n)
    if not pool:
        logger.warning("No stocks passed selection on %s", trade_date)
        return []

    # 2. Generate signals (no current positions for simplicity)
    timing = TrendTiming()
    signals = timing.generate_signals(
        trade_date=trade_date,
        stock_pool=pool,
        current_positions=[],
        portfolio_value=portfolio_value,
    )
    return signals
