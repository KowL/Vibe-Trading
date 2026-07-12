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
from src.ashare.strategies.market_models import (
    MatchedSymbol,
    StrategyRunRequest,
    StrategySnapshot,
)

router = APIRouter(prefix="/strategy-market", tags=["strategy-market"])


class RefreshRequest(BaseModel):
    strategy_id: str | None = None
    market_date: date | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    run_backtest: bool = True


SIGNAL_WEIGHTS = {"buy": 3.0, "sell": -3.0, "hold": 1.0, "watch": 0.5}


def _aggregate_consensus(
    snapshots: list[StrategySnapshot],
    strategy_names: dict[str, str],
    top_n: int = 20,
) -> list[dict[str, Any]]:
    """Aggregate matched symbols across strategies into a consensus ranking.

    Scoring:
      - base signal weight (buy/sell/hold/watch)
      - multiplied by confidence
      - plus raw score contribution when available
      - bonus for being endorsed by multiple strategies
    """
    by_symbol: dict[str, dict[str, Any]] = {}

    for snap in snapshots:
        if snap.status != "success":
            continue
        for m in snap.matched:
            if not isinstance(m, MatchedSymbol):
                continue
            symbol = m.symbol
            entry = by_symbol.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "name": m.name,
                    "signals": [],
                    "score_sum": 0.0,
                    "confidence_sum": 0.0,
                    "raw_score_sum": 0.0,
                    "count": 0,
                },
            )
            signal = m.signal
            weight = SIGNAL_WEIGHTS.get(signal, 0.0)
            confidence = max(0.0, min(1.0, m.confidence or 0.0))
            raw_score = m.score if m.score is not None else 0.0

            entry["signals"].append(
                {
                    "strategy_id": snap.strategy_id,
                    "strategy_name": strategy_names.get(snap.strategy_id, snap.strategy_id),
                    "signal": signal,
                    "score": raw_score,
                    "confidence": confidence,
                }
            )
            entry["score_sum"] += weight * confidence
            entry["confidence_sum"] += confidence
            entry["raw_score_sum"] += raw_score
            entry["count"] += 1

    # Build final consensus list
    results = []
    for entry in by_symbol.values():
        count = entry["count"]
        endorsement_bonus = 1.0 + 0.25 * (count - 1)
        consensus_score = (
            entry["score_sum"] * endorsement_bonus
            + entry["raw_score_sum"] * 0.1
        )

        # Determine dominant signal by weighted vote
        signal_votes: dict[str, float] = {}
        for s in entry["signals"]:
            w = SIGNAL_WEIGHTS.get(s["signal"], 0.0)
            signal_votes[s["signal"]] = signal_votes.get(s["signal"], 0.0) + w * s["confidence"]
        dominant_signal = max(signal_votes, key=signal_votes.get) if signal_votes else "watch"

        results.append(
            {
                "symbol": entry["symbol"],
                "name": entry["name"],
                "consensus_score": round(consensus_score, 4),
                "dominant_signal": dominant_signal,
                "strategy_count": count,
                "avg_confidence": round(entry["confidence_sum"] / count, 4) if count else 0.0,
                "signals": entry["signals"],
                "action_suggestion": _consensus_suggestion(
                    dominant_signal, consensus_score, count
                ),
            }
        )

    results.sort(key=lambda x: x["consensus_score"], reverse=True)
    return results[:top_n]


def _consensus_suggestion(signal: str, score: float, count: int) -> str:
    if signal == "buy":
        if count >= 3:
            return f"{count} 个策略共振看多，综合评分 {score:.2f}，可作为重点买入候选"
        return f"综合评分 {score:.2f}，今日可作为买入候选，注意仓位控制"
    if signal == "sell":
        return f"综合评分 {score:.2f}，出现卖出信号共振，建议止盈或减仓"
    if signal == "hold":
        return "多个策略提示持仓，建议按各自策略参数持有"
    return f"{count} 个策略纳入观察，综合评分 {score:.2f}，建议继续跟踪"


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


@router.get("/consensus")
async def strategy_consensus(top_n: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    """Return a consensus ranking of stocks endorsed by multiple strategies.

    Combines the latest successful strategy snapshots and scores each symbol by
    signal strength, confidence, and cross-strategy endorsement.
    """
    engine = get_market_engine()
    strategy_names = {d.id: d.name for d in engine.catalogue()}
    snapshots = [engine.get_snapshot(sid) for sid in engine.market_strategy_ids()]
    snapshots = [s for s in snapshots if s is not None]
    ranked = _aggregate_consensus(snapshots, strategy_names, top_n=top_n)
    return {
        "top_n": top_n,
        "count": len(ranked),
        "market_date": (
            snapshots[0].market_date.isoformat()
            if snapshots and snapshots[0].market_date
            else None
        ),
        "ranked": ranked,
    }
