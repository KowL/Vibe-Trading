"""A-share strategy market REST API routes.

Endpoints:
    GET  /ashare/strategy-market              full market state
    GET  /ashare/strategy-market/catalogue    strategy catalogue
    GET  /ashare/strategy-market/snapshots/{strategy_id}
    POST /ashare/strategy-market/refresh      refresh one or all strategies
    GET  /ashare/strategy-market/symbols/{strategy_id}
                                              matched symbols only
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.ashare.strategies.market_engine import get_market_engine
from src.ashare.strategies.market_models import StrategyRunRequest

router = APIRouter(prefix="/strategy-market", tags=["strategy-market"])


class RefreshRequest(BaseModel):
    strategy_id: str | None = None
    market_date: date | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    run_backtest: bool = True


@router.get("")
async def market_state() -> dict[str, Any]:
    """Return the full strategy market state."""
    engine = get_market_engine()
    state = engine.get_state()
    return {
        "strategies": [s.model_dump() for s in state.strategies],
        "snapshots": {
            sid: snap.model_dump() for sid, snap in state.snapshots.items()
        },
        "last_updated": state.last_updated.isoformat() if state.last_updated else None,
    }


@router.get("/catalogue")
async def market_catalogue() -> list[dict[str, Any]]:
    """Return the list of available strategies."""
    engine = get_market_engine()
    return [d.model_dump() for d in engine.catalogue()]


@router.get("/snapshots/{strategy_id}")
async def get_snapshot(strategy_id: str) -> dict[str, Any]:
    """Return the cached snapshot for a strategy."""
    engine = get_market_engine()
    snapshot = engine.get_snapshot(strategy_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return snapshot.model_dump()


@router.get("/symbols/{strategy_id}")
async def get_matched_symbols(strategy_id: str) -> dict[str, Any]:
    """Return matched symbols for a strategy."""
    engine = get_market_engine()
    snapshot = engine.get_snapshot(strategy_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return {
        "strategy_id": strategy_id,
        "market_date": snapshot.market_date.isoformat() if snapshot.market_date else None,
        "status": snapshot.status,
        "matched": [m.model_dump() for m in snapshot.matched],
    }


@router.post("/refresh")
async def refresh_market(body: RefreshRequest) -> dict[str, Any]:
    """Refresh one strategy or all strategies.

    If `strategy_id` is omitted, every registered strategy is refreshed.
    """
    engine = get_market_engine()
    if body.strategy_id:
        snapshot = await engine.refresh(
            strategy_id=body.strategy_id,
            market_date=body.market_date,
            params=body.params,
            run_backtest=body.run_backtest,
        )
        return {"refreshed": [snapshot.strategy_id], "snapshots": {snapshot.strategy_id: snapshot.model_dump()}}

    results = await engine.refresh_all(
        market_date=body.market_date,
        params=body.params,
        run_backtest=body.run_backtest,
    )
    return {
        "refreshed": list(results.keys()),
        "snapshots": {sid: snap.model_dump() for sid, snap in results.items()},
    }
