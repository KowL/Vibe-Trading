"""A-share market report generator.

Ports Ruo.ai's three report cadences to Vibe-Trading:
* 开盘报告 (09:00 Shanghai) — pre-market summary
* 收盘复盘 (18:01 Shanghai) — daily wrap-up
* 周度复盘 (Friday 19:01 Shanghai) — weekly summary

Reports are persisted as Markdown under ~/.vibe-trading/ashare/reports/
and exposed via the API for delivery to Feishu / Lark.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import httpx

from src.ashare.models.limit_up import LimitUpDaily
from src.ashare.storage.limit_up_store import LimitUpStore

logger = logging.getLogger(__name__)

_AMAZINGDATA_BASE = "http://127.0.0.1:3100"


class ReportKind(str, Enum):
    OPEN = "open"
    CLOSE = "close"
    WEEKLY = "weekly"


@dataclass
class MarketReport:
    """A generated market report."""

    kind: ReportKind
    trade_date: date
    title: str
    markdown: str
    metrics: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "trade_date": self.trade_date.isoformat(),
            "title": self.title,
            "markdown": self.markdown,
            "metrics": self.metrics,
            "created_at": self.created_at,
        }


@dataclass
class MarketMetrics:
    """Raw metrics collected before report generation."""

    trade_date: date
    limit_up_count: int
    limit_up_opened_count: int
    max_limit_up_count: int
    total_seal_amount: float
    leading_symbol: str = ""
    leading_name: str = ""
    index_quote: dict[str, float] = None  # type: ignore[assignment]
    hot_concepts: list[dict[str, Any]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.index_quote is None:
            self.index_quote = {}
        if self.hot_concepts is None:
            self.hot_concepts = []


def _today_shanghai() -> date:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _reports_dir() -> Path:
    d = Path.home() / ".vibe-trading" / "ashare" / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _report_path(kind: ReportKind, trade_date: date) -> Path:
    return _reports_dir() / f"{kind.value}_{trade_date.isoformat()}.md"


def _fetch_index_quote(symbol: str) -> dict[str, float]:
    """Fetch a single index quote from AmazingData /stock/quote/{code}."""
    try:
        r = httpx.get(
            f"{_AMAZINGDATA_BASE}/stock/quote/{symbol}",
            timeout=10.0,
        )
        r.raise_for_status()
        payload = r.json()
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(data, dict):
            return {}
        return {
            "price": float(data.get("last", 0) or data.get("close", 0)),
            "change_pct": float(data.get("changePct", 0)),
            "open": float(data.get("open", 0)),
            "high": float(data.get("high", 0)),
            "low": float(data.get("low", 0)),
        }
    except Exception as exc:
        logger.warning("index quote failed for %s: %s", symbol, exc)
        return {}


def _collect_metrics(trade_date: date, store: LimitUpStore) -> MarketMetrics:
    """Collect raw metrics for report generation."""
    records = list(store.load_day(trade_date).values())
    if not records:
        return MarketMetrics(trade_date=trade_date, limit_up_count=0, limit_up_opened_count=0, max_limit_up_count=0, total_seal_amount=0.0)

    sealed = [r for r in records if r.is_sealed]
    opened = [r for r in records if r.is_opened]
    max_count = max((r.limit_up_count for r in records), default=0)
    leader = max(records, key=lambda r: (r.limit_up_count, r.seal_amount))

    concepts: dict[str, dict[str, Any]] = {}
    for r in records:
        for c in str(r.concept or "").split(","):
            c = c.strip()
            if not c:
                continue
            entry = concepts.setdefault(c, {"name": c, "count": 0, "seal_amount": 0.0})
            entry["count"] += 1
            entry["seal_amount"] += r.seal_amount or 0

    hot_concepts = sorted(concepts.values(), key=lambda x: (x["count"], x["seal_amount"] or 0), reverse=True)[:10]

    return MarketMetrics(
        trade_date=trade_date,
        limit_up_count=len(sealed),
        limit_up_opened_count=len(opened),
        max_limit_up_count=max_count,
        total_seal_amount=sum((r.seal_amount or 0) for r in sealed),
        leading_symbol=leader.symbol,
        leading_name=leader.name,
        index_quote=_fetch_index_quote("000001.SH"),
        hot_concepts=hot_concepts,
    )


def _render_open_report(metrics: MarketMetrics) -> str:
    lines = [
        f"# A股开盘报告 — {metrics.trade_date.isoformat()}",
        "",
        "## 市场情绪",
        "",
        f"- 上证指数: {metrics.index_quote.get('price', 0):.2f} ({metrics.index_quote.get('change_pct', 0):+.2f}%)",
        f"- 昨日涨停数: {metrics.limit_up_count}",
        f"- 昨日炸板数: {metrics.limit_up_opened_count}",
        f"- 最高连板: {metrics.max_limit_up_count} 板",
        f"- 总封单金额: ¥{metrics.total_seal_amount:,.0f}",
        "",
        "## 昨日龙头",
        "",
        f"- {metrics.leading_symbol} {metrics.leading_name} — {metrics.max_limit_up_count} 连板",
        "",
        "## 热门概念 TOP10",
        "",
        "| 概念 | 涨停家数 | 封单金额 |",
        "|------|----------|----------|",
    ]
    for c in metrics.hot_concepts:
        lines.append(f"| {c['name']} | {c['count']} | ¥{c['seal_amount']:,.0f} |")
    lines.append("")
    lines.append("*数据来源: AmazingData / Vibe-Trading A-share extension*")
    lines.append("")
    return "\n".join(lines)


def _render_close_report(metrics: MarketMetrics) -> str:
    lines = [
        f"# A股收盘复盘 — {metrics.trade_date.isoformat()}",
        "",
        "## 涨停概览",
        "",
        f"- 涨停家数: {metrics.limit_up_count}",
        f"- 炸板家数: {metrics.limit_up_opened_count}",
        f"- 最高连板: {metrics.max_limit_up_count} 板",
        f"- 总封单金额: ¥{metrics.total_seal_amount:,.0f}",
        "",
        "## 龙头追踪",
        "",
        f"- {metrics.leading_symbol} {metrics.leading_name} — {metrics.max_limit_up_count} 连板",
        "",
        "## 热门概念 TOP10",
        "",
        "| 概念 | 涨停家数 | 封单金额 |",
        "|------|----------|----------|",
    ]
    for c in metrics.hot_concepts:
        lines.append(f"| {c['name']} | {c['count']} | ¥{c['seal_amount']:,.0f} |")
    lines.append("")
    lines.append("*数据来源: AmazingData / Vibe-Trading A-share extension*")
    lines.append("")
    return "\n".join(lines)


def _render_weekly_report(metrics: MarketMetrics, week_start: date, week_end: date) -> str:
    lines = [
        f"# A股周度复盘 — {week_start.isoformat()} ~ {week_end.isoformat()}",
        "",
        "## 本周收官",
        "",
        f"- 周五涨停家数: {metrics.limit_up_count}",
        f"- 周五炸板家数: {metrics.limit_up_opened_count}",
        f"- 当前最高连板: {metrics.max_limit_up_count} 板",
        f"- {metrics.leading_symbol} {metrics.leading_name}",
        "",
        "## 热门概念 TOP10",
        "",
        "| 概念 | 涨停家数 | 封单金额 |",
        "|------|----------|----------|",
    ]
    for c in metrics.hot_concepts:
        lines.append(f"| {c['name']} | {c['count']} | ¥{c['seal_amount']:,.0f} |")
    lines.append("")
    lines.append("*数据来源: AmazingData / Vibe-Trading A-share extension*")
    lines.append("")
    return "\n".join(lines)


class MarketReportTask:
    """Generate A-share market reports and persist them as Markdown."""

    def __init__(self, store: LimitUpStore | None = None) -> None:
        self.store = store if store is not None else LimitUpStore()

    async def run(self, kind: ReportKind, trade_date: date | None = None) -> MarketReport:
        """Generate and persist a market report.

        Args:
            kind: Which report to generate.
            trade_date: Defaults to today (Shanghai TZ).

        Returns:
            The generated report.
        """
        if trade_date is None:
            trade_date = _today_shanghai()

        metrics = _collect_metrics(trade_date, self.store)

        if kind == ReportKind.OPEN:
            title = f"A股开盘报告 — {trade_date.isoformat()}"
            markdown = _render_open_report(metrics)
        elif kind == ReportKind.CLOSE:
            title = f"A股收盘复盘 — {trade_date.isoformat()}"
            markdown = _render_close_report(metrics)
        elif kind == ReportKind.WEEKLY:
            week_end = trade_date
            week_start = week_end - timedelta(days=week_end.weekday())
            title = f"A股周度复盘 — {week_start.isoformat()} ~ {week_end.isoformat()}"
            markdown = _render_weekly_report(metrics, week_start, week_end)
        else:
            raise ValueError(f"unknown report kind: {kind}")

        report = MarketReport(
            kind=kind,
            trade_date=trade_date,
            title=title,
            markdown=markdown,
            metrics={
                "limit_up_count": metrics.limit_up_count,
                "limit_up_opened_count": metrics.limit_up_opened_count,
                "max_limit_up_count": metrics.max_limit_up_count,
                "total_seal_amount": metrics.total_seal_amount,
                "leading_symbol": metrics.leading_symbol,
                "leading_name": metrics.leading_name,
                "index_quote": metrics.index_quote,
                "hot_concepts": metrics.hot_concepts,
            },
            created_at=datetime.utcnow().isoformat(),
        )

        path = _report_path(kind, trade_date)
        path.write_text(markdown, encoding="utf-8")
        logger.info("wrote %s report to %s", kind.value, path)
        return report
