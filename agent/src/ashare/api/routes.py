"""A-share REST API routes for Vibe-Trading.

Endpoints:
    GET  /ashare/limit-up/{trade_date}     list limit-up records for a day
    POST /ashare/limit-up/sync            trigger limit-up sync
    GET  /ashare/portfolios               list portfolios
    POST /ashare/portfolios               create portfolio
    GET  /ashare/portfolios/{id}          get portfolio
    GET  /ashare/portfolios/{id}/trades   list trades
    POST /ashare/portfolios/{id}/trades   record a trade
    POST /ashare/reports/{kind}           generate market report (open/close/weekly)
    GET  /ashare/reports/{kind}/{date}    fetch persisted report markdown
    GET  /ashare/events                   SSE stream for real-time market events
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.ashare.api import market_routes
from src.ashare.backtest.limit_up_backtest import run_limit_up_backtest
from src.ashare.models.limit_up import LimitUpDaily
from src.ashare.models.portfolio import Portfolio, Trade, TradeSide
from src.ashare.storage.limit_up_store import LimitUpStore
from src.ashare.storage.portfolio_store import PortfolioStore
from src.ashare.tasks.limit_up_sync import LimitUpSyncTask
from src.ashare.tasks.market_report import MarketReportTask, ReportKind

router = APIRouter(prefix="/ashare", tags=["ashare"])
router.include_router(market_routes.router)

limit_up_store = LimitUpStore()
portfolio_store = PortfolioStore()
limit_up_task = LimitUpSyncTask(limit_up_store)
report_task = MarketReportTask(limit_up_store)


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class LimitUpRecordOut(BaseModel):
    trade_date: str
    symbol: str
    name: str
    limit_up_count: int
    limit_up_price: float
    open_price: float = 0.0
    close_price: float
    high_price: float = 0.0
    low_price: float = 0.0
    prev_close: float = 0.0
    change_pct: float
    turnover_amount: float = 0.0
    turnover_volume: float = 0.0
    turnover_ratio: float = 0.0
    seal_amount: float | None
    seal_ratio: float | None
    first_time: str | None
    last_time: str | None = None
    open_count: int | None = None
    industry: str | None
    concept: str | None
    reason: str | None = None
    is_sealed: bool


class LimitUpSyncOut(BaseModel):
    trade_date: str
    count: int
    source: str
    errors: list[str]


class PortfolioCreateIn(BaseModel):
    name: str = Field(default="A股模拟账户")
    initial_cash: float = Field(default=300_000.0)


class PortfolioOut(BaseModel):
    portfolio_id: str
    name: str
    initial_cash: float
    cash: float
    market_value: float
    total_value: float
    total_pnl: float
    total_return_pct: float


class TradeCreateIn(BaseModel):
    symbol: str
    side: TradeSide
    quantity: int
    price: float
    fee: float = 0.0


class TradeOut(BaseModel):
    trade_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    amount: float
    status: str
    pnl: float


class ReportOut(BaseModel):
    kind: str
    trade_date: str
    title: str
    markdown: str
    metrics: dict[str, Any]
    created_at: str


# --------------------------------------------------------------------------- #
# Limit-up routes
# --------------------------------------------------------------------------- #


@router.get("/limit-up/{trade_date}", response_model=list[LimitUpRecordOut])
def list_limit_up(trade_date: date) -> list[LimitUpRecordOut]:
    records = limit_up_store.load_day(trade_date)
    return [
        LimitUpRecordOut(
            trade_date=r.trade_date.isoformat(),
            symbol=r.symbol,
            name=r.name,
            limit_up_count=r.limit_up_count,
            limit_up_price=r.limit_up_price,
            open_price=r.open_price,
            close_price=r.close_price,
            high_price=r.high_price,
            low_price=r.low_price,
            prev_close=r.prev_close,
            change_pct=r.change_pct,
            turnover_amount=r.turnover_amount,
            turnover_volume=r.turnover_volume,
            turnover_ratio=r.turnover_ratio,
            seal_amount=r.seal_amount,
            seal_ratio=r.seal_ratio,
            first_time=r.first_time.isoformat() if r.first_time else None,
            last_time=r.last_time.isoformat() if r.last_time else None,
            open_count=r.open_count,
            industry=r.industry,
            concept=r.concept,
            reason=r.reason,
            is_sealed=r.is_sealed,
        )
        for r in records.values()
    ]


@router.post("/limit-up/sync", response_model=LimitUpSyncOut)
async def sync_limit_up(trade_date: date | None = Query(default=None)) -> LimitUpSyncOut:
    result = await limit_up_task.run(trade_date)
    return LimitUpSyncOut(
        trade_date=result.trade_date.isoformat(),
        count=result.count,
        source=result.source,
        errors=result.errors,
    )


# --------------------------------------------------------------------------- #
# Portfolio routes
# --------------------------------------------------------------------------- #


@router.get("/portfolios", response_model=list[PortfolioOut])
def list_portfolios() -> list[PortfolioOut]:
    portfolios = portfolio_store.list_portfolios()
    return [
        PortfolioOut(
            portfolio_id=p.portfolio_id,
            name=p.name,
            initial_cash=p.initial_cash,
            cash=p.cash,
            market_value=p.market_value,
            total_value=p.total_value,
            total_pnl=p.total_pnl,
            total_return_pct=p.total_return_pct,
        )
        for p in portfolios
    ]


@router.post("/portfolios", response_model=PortfolioOut)
def create_portfolio(body: PortfolioCreateIn) -> PortfolioOut:
    portfolio = Portfolio(
        portfolio_id=portfolio_store.new_portfolio_id(),
        name=body.name,
        initial_cash=body.initial_cash,
        cash=body.initial_cash,
    )
    portfolio_store.save_portfolio(portfolio)
    return PortfolioOut(
        portfolio_id=portfolio.portfolio_id,
        name=portfolio.name,
        initial_cash=portfolio.initial_cash,
        cash=portfolio.cash,
        market_value=portfolio.market_value,
        total_value=portfolio.total_value,
        total_pnl=portfolio.total_pnl,
        total_return_pct=portfolio.total_return_pct,
    )


@router.get("/portfolios/{portfolio_id}", response_model=PortfolioOut)
def get_portfolio(portfolio_id: str) -> PortfolioOut:
    try:
        p = portfolio_store.load_portfolio(portfolio_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return PortfolioOut(
        portfolio_id=p.portfolio_id,
        name=p.name,
        initial_cash=p.initial_cash,
        cash=p.cash,
        market_value=p.market_value,
        total_value=p.total_value,
        total_pnl=p.total_pnl,
        total_return_pct=p.total_return_pct,
    )


@router.get("/portfolios/{portfolio_id}/trades", response_model=list[TradeOut])
def list_trades(portfolio_id: str) -> list[TradeOut]:
    trades = portfolio_store.load_trades(portfolio_id)
    return [
        TradeOut(
            trade_id=t.trade_id,
            symbol=t.symbol,
            side=t.side.value,
            quantity=t.quantity,
            price=t.price,
            amount=t.amount,
            status=t.status.value,
            pnl=t.pnl,
        )
        for t in trades
    ]


@router.post("/portfolios/{portfolio_id}/trades", response_model=TradeOut)
def record_trade(portfolio_id: str, body: TradeCreateIn) -> TradeOut:
    try:
        portfolio = portfolio_store.load_portfolio(portfolio_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    import uuid

    trade = Trade(
        trade_id=f"tr_{uuid.uuid4().hex[:8]}",
        portfolio_id=portfolio_id,
        symbol=body.symbol,
        side=body.side,
        quantity=body.quantity,
        price=body.price,
        fee=body.fee,
    )
    portfolio_store.append_trade(portfolio_id, trade)

    # Update cash for simple paper accounting
    if body.side == TradeSide.BUY:
        portfolio.cash -= trade.amount + body.fee
    else:
        portfolio.cash += trade.amount - body.fee
    portfolio.update_metrics()
    portfolio_store.save_portfolio(portfolio)

    return TradeOut(
        trade_id=trade.trade_id,
        symbol=trade.symbol,
        side=trade.side.value,
        quantity=trade.quantity,
        price=trade.price,
        amount=trade.amount,
        status=trade.status.value,
        pnl=trade.pnl,
    )


# --------------------------------------------------------------------------- #
# Report routes
# --------------------------------------------------------------------------- #


@router.post("/reports/{kind}", response_model=ReportOut)
async def generate_report(kind: ReportKind, trade_date: date | None = Query(default=None)) -> ReportOut:
    report = await report_task.run(kind, trade_date)
    return ReportOut(
        kind=report.kind.value,
        trade_date=report.trade_date.isoformat(),
        title=report.title,
        markdown=report.markdown,
        metrics=report.metrics,
        created_at=report.created_at,
    )


@router.get("/reports/{kind}/{trade_date}", response_model=ReportOut)
def get_report(kind: ReportKind, trade_date: date) -> ReportOut:
    from src.ashare.tasks.market_report import _load_report_from_disk

    report = _load_report_from_disk(kind, trade_date)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return ReportOut(
        kind=report.kind.value,
        trade_date=report.trade_date.isoformat(),
        title=report.title,
        markdown=report.markdown,
        metrics=report.metrics,
        created_at=report.created_at,
    )


# --------------------------------------------------------------------------- #
# SSE events
# --------------------------------------------------------------------------- #

@router.get("/events")
async def ashare_events():
    """SSE stream for A-share real-time market events.

    Events:
        - ashare_limit_up_sync: 涨停数据同步完成
        - ashare_market_report: 市场报告生成完成
        - ashare_scheduler_heartbeat: 调度器心跳
    """
    from src.ashare.live_publisher import get_publisher
    from src.session.events import SSEEvent

    pub = get_publisher()

    async def event_generator():
        # Send initial connection event
        yield SSEEvent(
            event_type="connected",
            data={"channel": "ashare", "timestamp": datetime.now().isoformat()},
        ).to_sse()

        # Subscribe to ashare_broadcast channel
        if pub.event_bus:
            async for event in pub.event_bus.subscribe("ashare_broadcast", replay_all=True):
                yield event.to_sse()
        else:
            # No event bus configured, send heartbeat only
            while True:
                await asyncio.sleep(30)
                yield SSEEvent(
                    event_type="heartbeat",
                    data={"ts": datetime.now().isoformat()},
                ).to_sse()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------- #
# Strategy routes
# --------------------------------------------------------------------------- #

class StrategyBacktestRequest(BaseModel):
    start_date: date
    end_date: date
    initial_cash: float = Field(default=1_000_000, ge=100_000)
    universe: list[str] | None = None


@router.get("/strategy/select")
def strategy_select(
    trade_date: date = Query(default_factory=date.today),
    top_n: int = Query(default=20, ge=5, le=100),
) -> dict[str, Any]:
    """Run multi-factor stock selection using local data."""
    from src.ashare.strategies.local_select import local_select
    pool = local_select(trade_date=trade_date, top_n=top_n)
    return {
        "trade_date": trade_date.isoformat(),
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
                "atr_14": round(s.atr_14, 4) if s.atr_14 else 0.0,
            }
            for s in pool
        ],
    }


@router.post("/strategy/backtest")
def strategy_backtest(body: StrategyBacktestRequest) -> dict[str, Any]:
    """Run multi-factor strategy backtest."""
    from src.ashare.strategies import FastMultiFactorBacktest
    bt = FastMultiFactorBacktest()
    bt.preload_data(start_date=body.start_date, end_date=body.end_date, universe=body.universe)
    result = bt.run(
        start_date=body.start_date,
        end_date=body.end_date,
        initial_cash=body.initial_cash,
    )
    return {
        "start_date": body.start_date.isoformat(),
        "end_date": body.end_date.isoformat(),
        "initial_cash": body.initial_cash,
        "final_value": round(result.final_value, 2),
        "total_return_pct": round(result.total_return_pct, 2),
        "annualized_return_pct": round(result.annualized_return_pct, 2),
        "max_drawdown_pct": round(result.max_drawdown_pct, 2),
        "sharpe_ratio": round(result.sharpe_ratio, 2),
        "win_rate": round(result.win_rate, 1),
        "profit_factor": round(result.profit_factor, 2),
        "num_trades": result.num_trades,
        "avg_holding_days": round(result.avg_holding_days, 1),
        "equity_curve": [
            {
                "date": e["date"],
                "total_value": round(e["total_value"], 2),
                "drawdown_pct": round(e["drawdown_pct"], 2),
                "num_positions": e["num_positions"],
            }
            for e in result.equity_curve
        ],
        "trades": result.trades,
    }


@router.get("/strategy/profile")
def strategy_profile(
    symbol: str = Query(...),
    lookback_days: int = Query(default=120, ge=30, le=500),
) -> dict[str, Any]:
    """Get stock personality profile and adaptive parameters."""
    from src.ashare.strategies import LocalKlineLoader, StockProfile, BandParams
    from datetime import datetime, timedelta

    end = datetime.now().strftime("%Y%m%d")
    begin = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")

    loader = LocalKlineLoader()
    df = loader.load(symbol, begin, end)
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")

    profile = StockProfile.from_bars(df, symbol=symbol)
    params = BandParams.from_profile(profile)

    return {
        "symbol": symbol,
        "profile": profile.to_dict(),
        "adaptive_params": params.to_dict(),
    }


# --------------------------------------------------------------------------- #
# Backtest routes
# --------------------------------------------------------------------------- #

class BacktestRequest(BaseModel):
    start_date: date
    end_date: date
    min_days: int = Field(default=2, ge=1, le=20)
    max_days: int = Field(default=10, ge=1, le=20)
    hold_days: int = Field(default=1, ge=1, le=10)


@router.post("/backtest/limit-up")
def run_backtest_limit_up(body: BacktestRequest) -> dict[str, Any]:
    """Run limit-up strategy backtest."""
    result = run_limit_up_backtest(
        start_date=body.start_date,
        end_date=body.end_date,
        min_days=body.min_days,
        max_days=body.max_days,
        hold_days=body.hold_days,
    )
    return result
