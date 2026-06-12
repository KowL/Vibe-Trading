"""LimitUpDaily model for A-share short-term trading.

Mirrors Ruo.ai's LimitUpDaily entity:
    trade_date/symbol/limit_up_count/seal_amount/first_time/last_time/...

Vibe-Trading has no equivalent; this fills the gap so the runner can reason
about 涨停/连板/炸板 rates and generate A-share-specific reports.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from typing import Any, Optional


@dataclass
class LimitUpDaily:
    """One symbol's limit-up record for a single trading day.

    Attributes:
        trade_date: Trading date (Shanghai TZ).
        symbol: Normalized symbol, e.g. "000001.SZ" or "600000.SH".
        name: Human-readable security name.
        limit_up_count: 连板高度 (1 = 首板, 2 = 二连板, ...).
        limit_up_price: 涨停价.
        open_price: 开盘价.
        close_price: 收盘价 (should equal limit_up_price when sealed).
        high_price: 日内最高价.
        low_price: 日内最低价.
        prev_close: 昨收价.
        change_pct: 涨跌幅 (e.g. 0.10 for +10%).
        turnover_amount: 成交额 (元).
        turnover_volume: 成交量 (股).
        turnover_ratio: 换手率 (e.g. 0.05 for 5%).
        seal_amount: 封单金额 (元).
        seal_ratio: 封单比 = seal_amount / turnover_amount.
        first_time: 首次涨停时间 (Shanghai time) or None if never sealed.
        last_time: 最后一次涨停时间 or None.
        open_count: 涨停开盘次数 (炸板后回封次数).
        industry: 所属行业.
        concept: 所属概念 (comma-separated or list).
        reason: 涨停原因 / 消息面.
        created_at: UTC ISO timestamp when this record was persisted.
        source: Data source identifier ("adshare", "tushare", ...).
    """

    trade_date: date
    symbol: str
    name: str = ""
    limit_up_count: int = 1
    limit_up_price: float = 0.0
    open_price: float = 0.0
    close_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    prev_close: float = 0.0
    change_pct: float = 0.0
    turnover_amount: float = 0.0
    turnover_volume: float = 0.0
    turnover_ratio: float = 0.0
    seal_amount: float | None = None
    seal_ratio: float | None = None
    first_time: time | None = None
    last_time: time | None = None
    open_count: int | None = None
    industry: str | None = None
    concept: str | None = None
    reason: str | None = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    source: str = "adshare"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict."""
        data = asdict(self)
        data["trade_date"] = self.trade_date.isoformat()
        data["first_time"] = self.first_time.isoformat() if self.first_time else None
        data["last_time"] = self.last_time.isoformat() if self.last_time else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LimitUpDaily":
        """Deserialize from dict."""
        data = dict(data)
        data["trade_date"] = date.fromisoformat(data["trade_date"])
        if data.get("first_time"):
            data["first_time"] = time.fromisoformat(data["first_time"])
        else:
            data["first_time"] = None
        if data.get("last_time"):
            data["last_time"] = time.fromisoformat(data["last_time"])
        else:
            data["last_time"] = None
        return cls(**data)

    @property
    def is_sealed(self) -> bool:
        """True when the limit-up held through market close."""
        return self.close_price >= self.limit_up_price * 0.9999

    @property
    def is_opened(self) -> bool:
        """True when the board was broken at least once."""
        return (self.open_count or 0) > 0
