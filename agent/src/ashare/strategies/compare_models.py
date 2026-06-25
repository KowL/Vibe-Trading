"""Pydantic models for the strategy compare API."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


class StrategyCompareShared(BaseModel):
    start_date: date
    end_date: date
    initial_cash: float = Field(ge=10_000, le=100_000_000)
    universe: Literal["csi300", "csi500", "csi1000", "all_a"]
    commission_bps: float = Field(ge=0, le=50)
    slippage_bps: float = Field(ge=0, le=50)

    @model_validator(mode="after")
    def _check_date_range(self) -> "StrategyCompareShared":
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        if (self.end_date - self.start_date).days > 365 * 5:
            raise ValueError("date range must not exceed 5 years")
        return self


class LocalSelectParams(BaseModel):
    selector_kind: Literal["local_select"] = "local_select"
    top_n: int = Field(ge=5, le=100, default=20)
    rebalance_days: int = Field(ge=1, le=60, default=5)


class MultiFactorParams(BaseModel):
    selector_kind: Literal["multi_factor"] = "multi_factor"
    top_n: int = Field(ge=5, le=100, default=20)
    rebalance_days: int = Field(ge=1, le=60, default=5)
    factor_weights: dict[str, float] | None = None


SelectorParams = Annotated[
    LocalSelectParams | MultiFactorParams,
    Field(discriminator="selector_kind"),
]


class StrategySpec(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    selector: Literal["local_select", "multi_factor"]
    params: SelectorParams

    def params_model(self) -> LocalSelectParams | MultiFactorParams:
        return self.params

    @model_validator(mode="before")
    @classmethod
    def _inject_selector_kind(cls, data: Any) -> Any:
        if isinstance(data, dict):
            selector = data.get("selector")
            params = data.get("params")
            if isinstance(params, dict) and "selector_kind" not in params and selector in ("local_select", "multi_factor"):
                params = dict(params)
                params["selector_kind"] = selector
                data = dict(data)
                data["params"] = params
        return data

    @model_validator(mode="after")
    def _check_selector_matches_params(self) -> "StrategySpec":
        kind = self.params.selector_kind
        if kind != self.selector:
            raise ValueError(f"selector '{self.selector}' does not match params.selector_kind '{kind}'")
        return self


class StrategyCompareRequest(BaseModel):
    shared: StrategyCompareShared
    strategies: list[StrategySpec] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def _check_unique_names(self) -> "StrategyCompareRequest":
        names = [s.name for s in self.strategies]
        if len(names) != len(set(names)):
            raise ValueError("strategy names must be unique")
        return self


class StrategyMetrics(BaseModel):
    name: str
    selector: str
    start_date: date
    end_date: date
    initial_cash: float
    final_value: float
    total_return_pct: float
    annualized_return_pct: float | None
    max_drawdown_pct: float
    sharpe: float
    profit_factor: float
    num_trades: int
    avg_holding_days: float


class CurvePoint(BaseModel):
    date: date
    total_value: float
    drawdown_pct: float
    num_positions: int


class AlignedCurve(BaseModel):
    name: str
    points: list[CurvePoint]


class AlignmentInfo(BaseModel):
    common_dates: list[date]
    per_strategy_dropped: dict[str, int]
    coverage_ratio: float
    warning: Literal["low_coverage"] | None = None


class StrategyCompareResponse(BaseModel):
    shared: StrategyCompareShared
    alignment: AlignmentInfo
    metrics: list[StrategyMetrics]
    curves: list[AlignedCurve]
