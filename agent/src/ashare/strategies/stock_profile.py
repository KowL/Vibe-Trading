"""Stock personality profiling for adaptive strategy parameters.

Analyzes historical K-line to compute:
- Volatility (HV): historical volatility
- Amplitude: average daily range
- Trend strength (ADX): directional movement
- Mean reversion speed: how fast price returns to mean
- Momentum persistence: trend continuation likelihood

Usage:
    profile = StockProfile.from_bars(df)
    params = BandParams.from_profile(profile)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class StockProfile:
    """Personality profile of a stock based on historical data."""

    symbol: str

    # Volatility metrics
    hv_20: float = 0.0  # 20-day annualized historical volatility (%)
    hv_60: float = 0.0  # 60-day
    atr_14: float = 0.0  # Average True Range
    atr_pct: float = 0.0  # ATR as % of price

    # Amplitude metrics
    avg_amplitude: float = 0.0  # average (high-low)/close * 100
    max_amplitude: float = 0.0  # max daily amplitude
    amplitude_95: float = 0.0  # 95th percentile amplitude

    # Trend strength
    adx_14: float = 0.0  # Average Directional Index
    di_plus: float = 0.0  # +DI
    di_minus: float = 0.0  # -DI
    trend_direction: str = "neutral"  # "up", "down", "neutral"

    # Mean reversion
    mean_reversion_halflife: float = 0.0  # days to revert 50%
    autocorrelation_1: float = 0.0  # 1-day return autocorrelation
    hurst_exponent: float = 0.5  # 0.5 = random, >0.5 = trending, <0.5 = mean-reverting

    # Momentum
    momentum_20d: float = 0.0  # 20-day return
    momentum_60d: float = 0.0  # 60-day return
    momentum_persistence: float = 0.0  # correlation between 20d and 60d momentum

    # Classification
    personality: str = "unknown"  # "trending", "mean_reverting", "volatile", "stable"
    risk_level: str = "medium"  # "low", "medium", "high", "extreme"

    @classmethod
    def from_bars(cls, df: pd.DataFrame, symbol: str = "") -> StockProfile:
        """Compute profile from K-line DataFrame.

        Args:
            df: DataFrame with columns open, high, low, close, volume
            symbol: stock code

        Returns:
            StockProfile
        """
        if len(df) < 60:
            logger.warning("Profile for %s: only %d bars, need >= 60", symbol, len(df))
            return cls(symbol=symbol)

        close = df["close"]
        high = df["high"]
        low = df["low"]

        profile = cls(symbol=symbol)

        # --- Volatility ---
        log_returns = np.log(close / close.shift(1)).dropna()
        profile.hv_20 = float(log_returns.iloc[-20:].std() * np.sqrt(252) * 100)
        profile.hv_60 = float(log_returns.iloc[-60:].std() * np.sqrt(252) * 100)

        # ATR
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        profile.atr_14 = float(tr.iloc[-14:].mean())
        profile.atr_pct = float(profile.atr_14 / close.iloc[-1] * 100)

        # --- Amplitude ---
        amplitude = ((high - low) / close * 100).dropna()
        profile.avg_amplitude = float(amplitude.mean())
        profile.max_amplitude = float(amplitude.max())
        profile.amplitude_95 = float(amplitude.quantile(0.95))

        # --- Trend (ADX) ---
        profile.adx_14, profile.di_plus, profile.di_minus = cls._compute_adx(high, low, close)
        if profile.di_plus > profile.di_minus + 10:
            profile.trend_direction = "up"
        elif profile.di_minus > profile.di_plus + 10:
            profile.trend_direction = "down"
        else:
            profile.trend_direction = "neutral"

        # --- Mean Reversion ---
        profile.autocorrelation_1 = float(log_returns.iloc[-60:].autocorr(lag=1) or 0)
        profile.hurst_exponent = cls._compute_hurst(log_returns.iloc[-60:].values)
        profile.mean_reversion_halflife = cls._compute_halflife(close.iloc[-60:])

        # --- Momentum ---
        profile.momentum_20d = float((close.iloc[-1] / close.iloc[-20] - 1) * 100)
        profile.momentum_60d = float((close.iloc[-1] / close.iloc[-60] - 1) * 100)
        if len(close) >= 120:
            mom20 = (close.iloc[-100:-20] / close.iloc[-120:-40] - 1).dropna()
            mom60 = (close.iloc[-100:-20] / close.iloc[-120:-40] - 1).dropna()
            if len(mom20) > 5 and len(mom60) > 5:
                profile.momentum_persistence = float(np.corrcoef(mom20.values, mom60.values)[0, 1])

        # --- Classification ---
        profile._classify()

        return profile

    def _classify(self) -> None:
        """Classify stock personality and risk level."""
        # Personality
        if self.hurst_exponent > 0.55 and self.adx_14 > 25:
            self.personality = "trending"
        elif self.hurst_exponent < 0.45 and self.autocorrelation_1 < -0.1:
            self.personality = "mean_reverting"
        elif self.hv_20 > 40 or self.avg_amplitude > 5:
            self.personality = "volatile"
        else:
            self.personality = "stable"

        # Risk level
        if self.hv_20 > 50 or self.atr_pct > 4:
            self.risk_level = "extreme"
        elif self.hv_20 > 35 or self.atr_pct > 3:
            self.risk_level = "high"
        elif self.hv_20 > 20 or self.atr_pct > 2:
            self.risk_level = "medium"
        else:
            self.risk_level = "low"

    @staticmethod
    def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series) -> tuple[float, float, float]:
        """Compute ADX, +DI, -DI."""
        try:
            tr1 = high - low
            tr2 = (high - close.shift(1)).abs()
            tr3 = (low - close.shift(1)).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

            dm_plus = high.diff()
            dm_minus = -low.diff()
            dm_plus = dm_plus.where((dm_plus > dm_minus) & (dm_plus > 0), 0)
            dm_minus = dm_minus.where((dm_minus > dm_plus) & (dm_minus > 0), 0)

            atr_14 = tr.rolling(14).mean()
            di_plus = 100 * dm_plus.rolling(14).mean() / atr_14
            di_minus = 100 * dm_minus.rolling(14).mean() / atr_14
            dx = (di_plus - di_minus).abs() / (di_plus + di_minus) * 100
            adx = dx.rolling(14).mean()

            return (
                float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 0.0,
                float(di_plus.iloc[-1]) if not pd.isna(di_plus.iloc[-1]) else 0.0,
                float(di_minus.iloc[-1]) if not pd.isna(di_minus.iloc[-1]) else 0.0,
            )
        except Exception:
            return 0.0, 0.0, 0.0

    @staticmethod
    def _compute_hurst(log_returns: np.ndarray) -> float:
        """Compute Hurst exponent using R/S analysis."""
        try:
            n = len(log_returns)
            if n < 20:
                return 0.5

            lags = range(2, min(n // 4, 20))
            tau = [np.std(np.subtract(log_returns[lag:], log_returns[:-lag])) for lag in lags]
            poly = np.polyfit(np.log(lags), np.log(tau), 1)
            return float(poly[0])
        except Exception:
            return 0.5

    @staticmethod
    def _compute_halflife(prices: pd.Series) -> float:
        """Compute mean reversion half-life using Ornstein-Uhlenbeck."""
        try:
            from statsmodels.regression.linear_model import OLS
            from statsmodels.tools import add_constant

            lag = prices.shift(1).dropna()
            delta = prices.diff().dropna()
            aligned = pd.concat([lag, delta], axis=1).dropna()
            if len(aligned) < 10:
                return 0.0

            X = add_constant(aligned.iloc[:, 0])
            y = aligned.iloc[:, 1]
            model = OLS(y, X).fit()
            beta = model.params.iloc[1]
            if beta >= 0:
                return 0.0  # not mean-reverting
            halflife = -np.log(2) / beta
            return float(halflife)
        except Exception:
            return 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "hv_20": round(self.hv_20, 2),
            "hv_60": round(self.hv_60, 2),
            "atr_14": round(self.atr_14, 4),
            "atr_pct": round(self.atr_pct, 2),
            "avg_amplitude": round(self.avg_amplitude, 2),
            "max_amplitude": round(self.max_amplitude, 2),
            "amplitude_95": round(self.amplitude_95, 2),
            "adx_14": round(self.adx_14, 2),
            "di_plus": round(self.di_plus, 2),
            "di_minus": round(self.di_minus, 2),
            "trend_direction": self.trend_direction,
            "mean_reversion_halflife": round(self.mean_reversion_halflife, 1),
            "autocorrelation_1": round(self.autocorrelation_1, 3),
            "hurst_exponent": round(self.hurst_exponent, 3),
            "momentum_20d": round(self.momentum_20d, 2),
            "momentum_60d": round(self.momentum_60d, 2),
            "momentum_persistence": round(self.momentum_persistence, 3),
            "personality": self.personality,
            "risk_level": self.risk_level,
        }

    def __str__(self) -> str:
        return (
            f"StockProfile({self.symbol}): {self.personality}, {self.risk_level} risk\n"
            f"  HV20={self.hv_20:.1f}%, ATR={self.atr_pct:.1f}%, Amp={self.avg_amplitude:.1f}%\n"
            f"  ADX={self.adx_14:.1f}, Trend={self.trend_direction}\n"
            f"  Hurst={self.hurst_exponent:.3f}, AC1={self.autocorrelation_1:.3f}\n"
            f"  Mom20={self.momentum_20d:.1f}%, Mom60={self.momentum_60d:.1f}%"
        )
