"""Portfolio / Trade model for A-share trading.

Mirrors Ruo.ai's Portfolio + Trade entities with Vibe-Trading idioms:
* file-system JSON persistence under ~/.vibe-trading/ashare/
* simple PnL tracking in CNY
* no broker integration (paper / shadow account only)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional


class TradeSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class TradeStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


@dataclass
class Trade:
    """One round-trip or leg of an A-share position.

    Attributes:
        trade_id: Stable unique ID.
        portfolio_id: Owning portfolio ID.
        symbol: Normalized symbol.
        side: buy or sell.
        quantity: 股数 (must be integer lots of 100 for normal A-shares).
        price: 成交价格.
        amount: 成交金额 = price * quantity.
        fee: 手续费 (commission + stamp tax estimate).
        trade_date: 成交日期.
        status: open / closed / cancelled.
        opened_at: UTC ISO timestamp.
        closed_at: Optional close timestamp.
        close_price: Optional realized close price.
        close_amount: Optional realized close amount.
        pnl: Realized PnL in CNY (sell only).
        notes: Human / LLM notes.
    """

    trade_id: str
    portfolio_id: str
    symbol: str
    side: TradeSide
    quantity: int
    price: float
    amount: float = 0.0
    fee: float = 0.0
    trade_date: date = field(default_factory=date.today)
    status: TradeStatus = TradeStatus.OPEN
    opened_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    closed_at: Optional[str] = None
    close_price: Optional[float] = None
    close_amount: Optional[float] = None
    pnl: float = 0.0
    notes: str = ""

    def __post_init__(self) -> None:
        if self.amount == 0.0 and self.price and self.quantity:
            object.__setattr__(
                self, "amount", round(self.price * self.quantity, 2)
            )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["side"] = self.side.value
        data["status"] = self.status.value
        data["trade_date"] = self.trade_date.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Trade":
        data = dict(data)
        data["side"] = TradeSide(data["side"])
        data["status"] = TradeStatus(data["status"])
        data["trade_date"] = date.fromisoformat(data["trade_date"])
        return cls(**data)

    def close(self, close_price: float, close_fee: float = 0.0) -> None:
        """Mark a buy trade as closed with a sell price."""
        if self.side != TradeSide.BUY or self.status != TradeStatus.OPEN:
            return
        self.close_price = close_price
        self.close_amount = round(close_price * self.quantity, 2)
        gross_pnl = (close_price - self.price) * self.quantity
        self.pnl = round(gross_pnl - self.fee - close_fee, 2)
        self.status = TradeStatus.CLOSED
        self.closed_at = datetime.utcnow().isoformat()


@dataclass
class Portfolio:
    """A paper / shadow portfolio for A-share trading.

    Attributes:
        portfolio_id: Stable unique ID.
        name: Display name.
        initial_cash: 初始资金.
        cash: 可用现金.
        market_value: 持仓市值 (updated on demand).
        total_value: 总资产 = cash + market_value.
        total_pnl: 累计盈亏.
        total_return_pct: 累计收益率.
        created_at: UTC ISO timestamp.
        updated_at: UTC ISO timestamp.
    """

    portfolio_id: str
    name: str = "A股模拟账户"
    initial_cash: float = 300_000.0
    cash: float = 300_000.0
    market_value: float = 0.0
    total_pnl: float = 0.0
    total_return_pct: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Portfolio":
        return cls(**data)

    @property
    def total_value(self) -> float:
        return round(self.cash + self.market_value, 2)

    def update_metrics(self) -> None:
        """Recalculate derived metrics after trades / price updates."""
        self.total_return_pct = round(
            (self.total_value - self.initial_cash) / self.initial_cash * 100, 2
        ) if self.initial_cash else 0.0
        self.updated_at = datetime.utcnow().isoformat()
