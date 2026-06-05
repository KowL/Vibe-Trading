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
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.ashare.models.limit_up import LimitUpDaily
from src.ashare.models.portfolio import Portfolio, Trade, TradeSide
from src.ashare.storage.limit_up_store import LimitUpStore
from src.ashare.storage.portfolio_store import PortfolioStore
from src.ashare.tasks.limit_up_sync import LimitUpSyncTask
from src.ashare.tasks.market_report import MarketReportTask, ReportKind

router = APIRouter(prefix="/ashare", tags=["ashare"])

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
    close_price: float
    change_pct: float
    seal_amount: float | None
    seal_ratio: float | None
    first_time: str | None
    industry: str | None
    concept: str | None
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
            close_price=r.close_price,
            change_pct=r.change_pct,
            seal_amount=r.seal_amount,
            seal_ratio=r.seal_ratio,
            first_time=r.first_time.isoformat() if r.first_time else None,
            industry=r.industry,
            concept=r.concept,
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


@router.get("/reports/{kind}/{trade_date}")
def get_report(kind: ReportKind, trade_date: date) -> dict[str, str]:
    path = Path.home() / ".vibe-trading" / "ashare" / "reports" / f"{kind.value}_{trade_date.isoformat()}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return {"kind": kind.value, "trade_date": trade_date.isoformat(), "markdown": path.read_text(encoding="utf-8")}
