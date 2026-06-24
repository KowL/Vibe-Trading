"""Data models for the A-share strategy market.

The strategy market exposes a catalogue of trading strategies, each producing a
`StrategySnapshot` with matched symbols, metrics and an optional equity curve.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class StrategyCategory(str, Enum):
    """Strategy taxonomy used by the market UI."""

    SELECTOR = "选股"
    TIMING = "择时"
    BAND = "波段"
    ADAPTIVE = "自适应"
    PROFILE = "个股画像"


class StrategyParam(BaseModel):
    """A single configurable parameter for a strategy."""

    id: str
    name: str
    type: Literal["int", "float", "str", "bool", "date"]
    default: Any
    min: float | None = None
    max: float | None = None
    description: str = ""


class StrategyDefinition(BaseModel):
    """Static metadata describing a strategy that can be traded in the market."""

    id: str
    name: str
    description: str
    category: StrategyCategory
    params: list[StrategyParam] = Field(default_factory=list)
    supports_backtest: bool = True
    supports_realtime: bool = True


class MatchedSymbol(BaseModel):
    """A symbol matched by a strategy run."""

    symbol: str
    signal: Literal["buy", "sell", "hold", "watch"]
    score: float | None = None
    confidence: float = 0.0
    rank: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StrategyMetrics(BaseModel):
    """Performance metrics derived from a backtest or live run."""

    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    num_trades: int = 0
    avg_holding_days: float = 0.0


class StrategySnapshot(BaseModel):
    """The result of running one strategy at a point in time."""

    strategy_id: str
    run_at: datetime
    status: Literal["running", "success", "error", "idle"]
    market_date: date | None = None
    matched: list[MatchedSymbol] = Field(default_factory=list)
    metrics: StrategyMetrics | None = None
    backtest_curve: list[dict[str, Any]] | None = None
    error: str | None = None


class StrategyRunRequest(BaseModel):
    """Request to run (or refresh) a single strategy."""

    strategy_id: str
    market_date: date | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    run_backtest: bool = True
    backtest_start: date | None = None
    backtest_end: date | None = None


class StrategyMarketState(BaseModel):
    """Full state of the strategy market returned by the API."""

    strategies: list[StrategyDefinition]
    snapshots: dict[str, StrategySnapshot]
    last_updated: datetime | None = None
