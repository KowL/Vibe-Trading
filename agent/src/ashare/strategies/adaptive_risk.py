"""Adaptive risk management based on stock personality profile.

Adjusts stop-loss, take-profit, position size, and holding period
based on StockProfile characteristics.

Usage:
    profile = StockProfile.from_bars(df, symbol="000001.SZ")
    params = BandParams.from_profile(profile)
    print(params)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.ashare.strategies.stock_profile import StockProfile

logger = logging.getLogger(__name__)


@dataclass
class BandParams:
    """Adaptive trading parameters for a specific stock.

    All percentages are in decimal form (e.g. 0.08 = 8%).
    """

    symbol: str

    # Stop loss (adaptive to volatility)
    stop_loss_pct: float = 0.08  # 8% default
    stop_loss_atr_mult: float = 2.0  # ATR multiplier

    # Take profit (adaptive to amplitude)
    take_profit_pct: float = 0.15  # 15% default
    take_profit_atr_mult: float = 3.0  # ATR multiplier

    # Position sizing (adaptive to trend strength)
    max_position_pct: float = 0.20  # max 20% of portfolio
    risk_per_trade_pct: float = 0.02  # 2% risk per trade

    # Holding period (adaptive to mean reversion)
    max_holding_days: int = 15  # force exit after N days
    trend_following_extend: bool = True  # extend if trend continues

    # Entry filters (adaptive to personality)
    min_momentum_pct: float = 2.0  # minimum 2% momentum
    min_volume_ratio: float = 1.2  # 20% above average
    require_trend_alignment: bool = True  # MA5 > MA20 > MA60

    # Trailing stop (for trending stocks)
    use_trailing_stop: bool = False
    trailing_stop_pct: float = 0.05  # 5% trailing

    @classmethod
    def from_profile(cls, profile: StockProfile) -> BandParams:
        """Generate adaptive parameters from stock profile.

        Args:
            profile: StockProfile with computed metrics

        Returns:
            BandParams tailored to the stock's personality
        """
        params = cls(symbol=profile.symbol)

        # --- Stop Loss: based on volatility ---
        # Low vol: tighter stop (-6% to -8%)
        # High vol: wider stop (-12% to -15%)
        if profile.hv_20 < 20:
            params.stop_loss_pct = 0.06
            params.stop_loss_atr_mult = 1.5
        elif profile.hv_20 < 30:
            params.stop_loss_pct = 0.08
            params.stop_loss_atr_mult = 2.0
        elif profile.hv_20 < 45:
            params.stop_loss_pct = 0.10
            params.stop_loss_atr_mult = 2.5
        else:
            params.stop_loss_pct = 0.15
            params.stop_loss_atr_mult = 3.0

        # --- Take Profit: based on amplitude ---
        # Low amplitude: modest target (+10% to +15%)
        # High amplitude: ambitious target (+25% to +35%)
        if profile.avg_amplitude < 2:
            params.take_profit_pct = 0.10
            params.take_profit_atr_mult = 2.0
        elif profile.avg_amplitude < 3.5:
            params.take_profit_pct = 0.15
            params.take_profit_atr_mult = 3.0
        elif profile.avg_amplitude < 5:
            params.take_profit_pct = 0.25
            params.take_profit_atr_mult = 4.0
        else:
            params.take_profit_pct = 0.35
            params.take_profit_atr_mult = 5.0

        # --- Position Size: based on trend strength ---
        # Strong trend: larger position (up to 40%)
        # Weak/uncertain: smaller position (10-15%)
        if profile.adx_14 > 30 and profile.trend_direction != "neutral":
            params.max_position_pct = 0.40
            params.risk_per_trade_pct = 0.03  # 3% risk
        elif profile.adx_14 > 20:
            params.max_position_pct = 0.25
            params.risk_per_trade_pct = 0.02
        else:
            params.max_position_pct = 0.15
            params.risk_per_trade_pct = 0.01  # 1% risk

        # --- Holding Period: based on mean reversion ---
        # Fast mean reversion: short hold (5-7 days)
        # Slow mean reversion / trending: longer hold (15-30 days)
        if profile.mean_reversion_halflife > 0 and profile.mean_reversion_halflife < 5:
            params.max_holding_days = 7
        elif profile.mean_reversion_halflife < 15 or profile.hurst_exponent > 0.55:
            params.max_holding_days = 15
        else:
            params.max_holding_days = 30

        # --- Trailing Stop: for trending stocks ---
        if profile.hurst_exponent > 0.55 and profile.adx_14 > 25:
            params.use_trailing_stop = True
            params.trailing_stop_pct = max(0.03, profile.atr_pct / 100 * 1.5)

        # --- Entry Filters: adaptive ---
        # Trending stocks: require stronger momentum
        # Mean-reverting stocks: allow weaker momentum
        if profile.personality == "trending":
            params.min_momentum_pct = 3.0
            params.require_trend_alignment = True
        elif profile.personality == "mean_reverting":
            params.min_momentum_pct = 0.5  # can buy on dips
            params.require_trend_alignment = False
        elif profile.personality == "volatile":
            params.min_momentum_pct = 2.0
            params.min_volume_ratio = 1.5  # require more volume confirmation
        else:  # stable
            params.min_momentum_pct = 1.0
            params.min_volume_ratio = 1.0

        logger.debug(
            "BandParams for %s: SL=%.1f%% TP=%.1f%% Pos=%.0f%% Hold=%dd",
            profile.symbol,
            params.stop_loss_pct * 100,
            params.take_profit_pct * 100,
            params.max_position_pct * 100,
            params.max_holding_days,
        )
        return params

    def compute_stop_loss(self, entry_price: float, atr: float) -> float:
        """Compute stop loss price."""
        atr_stop = entry_price - atr * self.stop_loss_atr_mult
        pct_stop = entry_price * (1 - self.stop_loss_pct)
        return min(atr_stop, pct_stop)  # tighter of the two

    def compute_take_profit(self, entry_price: float, atr: float) -> float:
        """Compute take profit price."""
        atr_tp = entry_price + atr * self.take_profit_atr_mult
        pct_tp = entry_price * (1 + self.take_profit_pct)
        return max(atr_tp, pct_tp)  # wider of the two

    def compute_position_size(
        self, portfolio_value: float, entry_price: float, stop_loss: float
    ) -> int:
        """Compute position size based on risk.

        Returns:
            Number of shares to buy
        """
        risk_amount = portfolio_value * self.risk_per_trade_pct
        price_risk = entry_price - stop_loss
        if price_risk <= 0:
            return 0
        max_shares_by_risk = int(risk_amount / price_risk)
        max_value = portfolio_value * self.max_position_pct
        max_shares_by_value = int(max_value / entry_price)
        return min(max_shares_by_risk, max_shares_by_value)

    def should_force_exit(self, days_held: int, current_pnl_pct: float) -> bool:
        """Check if position should be force-closed.

        Args:
            days_held: number of days held
            current_pnl_pct: current profit/loss percentage

        Returns:
            True if should exit
        """
        # Time-based exit
        if days_held >= self.max_holding_days:
            return True
        # Trailing stop check (simplified)
        if self.use_trailing_stop and current_pnl_pct > 0:
            # Would need peak price tracking for proper trailing stop
            pass
        return False

    def __str__(self) -> str:
        return (
            f"BandParams({self.symbol}):\n"
            f"  Stop Loss: {self.stop_loss_pct*100:.1f}% (ATR×{self.stop_loss_atr_mult})\n"
            f"  Take Profit: {self.take_profit_pct*100:.1f}% (ATR×{self.take_profit_atr_mult})\n"
            f"  Position: max {self.max_position_pct*100:.0f}% of portfolio, "
            f"risk {self.risk_per_trade_pct*100:.1f}% per trade\n"
            f"  Holding: max {self.max_holding_days} days\n"
            f"  Entry: momentum >{self.min_momentum_pct:.1f}%, vol_ratio >{self.min_volume_ratio:.1f}x\n"
            f"  Trailing: {'ON' if self.use_trailing_stop else 'OFF'}"
        )

    def to_dict(self) -> dict[str, float | int | bool | str]:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "stop_loss_pct": round(self.stop_loss_pct, 4),
            "stop_loss_atr_mult": round(self.stop_loss_atr_mult, 2),
            "take_profit_pct": round(self.take_profit_pct, 4),
            "take_profit_atr_mult": round(self.take_profit_atr_mult, 2),
            "max_position_pct": round(self.max_position_pct, 4),
            "risk_per_trade_pct": round(self.risk_per_trade_pct, 4),
            "max_holding_days": self.max_holding_days,
            "use_trailing_stop": self.use_trailing_stop,
            "trailing_stop_pct": round(self.trailing_stop_pct, 4),
            "min_momentum_pct": round(self.min_momentum_pct, 2),
            "min_volume_ratio": round(self.min_volume_ratio, 2),
            "require_trend_alignment": self.require_trend_alignment,
        }
