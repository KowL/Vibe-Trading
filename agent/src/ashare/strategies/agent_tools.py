"""Agent tools for multi-factor strategy.

Registers tools for:
- Multi-factor stock selection
- Strategy backtest
- Stock personality profiling
- Adaptive parameter generation
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from src.agent.tools import BaseTool
from src.ashare.strategies import (
    FastMultiFactorBacktest,
    MultiFactorSelector,
    StockProfile,
    BandParams,
    LocalKlineLoader,
)

logger = logging.getLogger(__name__)


class MultiFactorSelectTool(BaseTool):
    """Select stocks using multi-factor model."""

    name = "ashare_multi_factor_select"
    description = "Select A-share stocks using multi-factor model (trend + momentum + volume + alpha factors)"
    parameters = {
        "trade_date": {
            "type": "string",
            "description": "Trade date in YYYY-MM-DD format",
        },
        "top_n": {
            "type": "integer",
            "description": "Number of top stocks to return",
            "default": 20,
        },
    }

    async def execute(self, trade_date: str = "", top_n: int = 20) -> dict[str, Any]:
        """Run multi-factor selection."""
        td = date.fromisoformat(trade_date) if trade_date else date.today()
        selector = MultiFactorSelector()
        pool = selector.select(trade_date=td, top_n=top_n)
        return {
            "trade_date": td.isoformat(),
            "selected_count": len(pool),
            "stocks": [
                {
                    "symbol": s.symbol,
                    "composite_score": round(s.composite_score, 3),
                    "momentum_20d": round(s.momentum_20d, 1),
                    "volume_ratio": round(s.volume_ratio, 2),
                    "ma5": round(s.ma5, 2),
                    "ma20": round(s.ma20, 2),
                    "ma60": round(s.ma60, 2),
                    "atr_14": round(s.atr_14, 4),
                }
                for s in pool
            ],
        }


class StrategyBacktestTool(BaseTool):
    """Run strategy backtest."""

    name = "ashare_strategy_backtest"
    description = "Run multi-factor + trend strategy backtest with local data"
    parameters = {
        "start_date": {
            "type": "string",
            "description": "Start date YYYY-MM-DD",
        },
        "end_date": {
            "type": "string",
            "description": "End date YYYY-MM-DD",
        },
        "initial_cash": {
            "type": "number",
            "description": "Initial capital",
            "default": 1_000_000,
        },
        "universe": {
            "type": "array",
            "description": "List of stock codes to test",
            "items": {"type": "string"},
        },
    }

    async def execute(
        self,
        start_date: str,
        end_date: str,
        initial_cash: float = 1_000_000,
        universe: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run backtest."""
        sd = date.fromisoformat(start_date)
        ed = date.fromisoformat(end_date)

        bt = FastMultiFactorBacktest()
        bt.preload_data(start_date=sd, end_date=ed, universe=universe)
        result = bt.run(start_date=sd, end_date=ed, initial_cash=initial_cash)

        return {
            "start_date": start_date,
            "end_date": end_date,
            "initial_cash": initial_cash,
            "final_value": round(result.final_value, 2),
            "total_return_pct": round(result.total_return_pct, 2),
            "annualized_return_pct": round(result.annualized_return_pct, 2),
            "max_drawdown_pct": round(result.max_drawdown_pct, 2),
            "sharpe_ratio": round(result.sharpe_ratio, 2),
            "win_rate": round(result.win_rate, 1),
            "profit_factor": round(result.profit_factor, 2),
            "num_trades": result.num_trades,
            "avg_holding_days": round(result.avg_holding_days, 1),
            "trades": result.trades[:10],  # First 10 trades
        }


class StockProfileTool(BaseTool):
    """Analyze stock personality profile."""

    name = "ashare_stock_profile"
    description = "Compute stock personality profile (volatility, trend, mean-reversion, momentum)"
    parameters = {
        "symbol": {
            "type": "string",
            "description": "Stock code with suffix (e.g. 000001.SZ)",
        },
        "lookback_days": {
            "type": "integer",
            "description": "Lookback period in days",
            "default": 120,
        },
    }

    async def execute(self, symbol: str, lookback_days: int = 120) -> dict[str, Any]:
        """Compute profile."""
        from datetime import datetime, timedelta

        end = datetime.now().strftime("%Y%m%d")
        begin = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")

        loader = LocalKlineLoader()
        df = loader.load(symbol, begin, end)
        if df is None:
            return {"error": f"No data for {symbol}"}

        profile = StockProfile.from_bars(df, symbol=symbol)
        params = BandParams.from_profile(profile)

        return {
            "symbol": symbol,
            "profile": profile.to_dict(),
            "adaptive_params": params.to_dict(),
        }
