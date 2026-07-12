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
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.ashare.models.limit_up import LimitUpDaily
from src.ashare.storage.limit_up_store import LimitUpStore
from src.ashare.tasks.report_llm import ReportLLMError, generate_report_analysis
from src.ashare.tasks.report_prompts import ReportKind
from src.ashare.tushare_client import TushareClient

logger = logging.getLogger(__name__)

_INDEX_SYMBOLS = ["000001.SH", "399001.SZ", "399006.SZ", "000688.SH"]
_INDEX_NAMES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
}


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
    limit_up_count: int = 0
    limit_up_opened_count: int = 0
    max_limit_up_count: int = 0
    total_seal_amount: float = 0.0
    leading_symbol: str = ""
    leading_name: str = ""
    index_quotes: dict[str, dict[str, float]] = field(default_factory=dict)
    hot_concepts: list[dict[str, Any]] = field(default_factory=list)
    hot_industries: list[dict[str, Any]] = field(default_factory=list)
    data_source: str = "AkShare"

    # Extended metrics
    first_board_count: int = 0
    second_board_count: int = 0
    third_board_count: int = 0
    fourth_plus_board_count: int = 0
    ever_limit_up_count: int = 0
    broken_rate: float = 0.0
    seal_tiers: dict[str, int] = field(default_factory=dict)
    strong_targets: list[dict[str, Any]] = field(default_factory=list)
    first_breakout_targets: list[dict[str, Any]] = field(default_factory=list)

    # Weekly-only fields
    daily_limit_up_counts: dict[str, int] = field(default_factory=dict)
    weekly_start: str = ""
    weekly_end: str = ""

    def __post_init__(self) -> None:
        if self.index_quotes is None:
            self.index_quotes = {}
        if self.hot_concepts is None:
            self.hot_concepts = []
        if self.hot_industries is None:
            self.hot_industries = []
        if self.seal_tiers is None:
            self.seal_tiers = {"极强": 0, "强": 0, "中": 0, "弱": 0}
        if self.strong_targets is None:
            self.strong_targets = []
        if self.first_breakout_targets is None:
            self.first_breakout_targets = []
        if self.daily_limit_up_counts is None:
            self.daily_limit_up_counts = {}

    def to_dict(self) -> dict[str, Any]:
        """Serialize metrics to a JSON-safe dict."""
        data = asdict(self)
        data["trade_date"] = self.trade_date.isoformat()
        return data


def _today_shanghai() -> date:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _reports_dir() -> Path:
    d = Path.home() / ".vibe-trading" / "ashare" / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _report_path(kind: ReportKind, trade_date: date) -> Path:
    return _reports_dir() / f"{kind.value}_{trade_date.isoformat()}.md"


def _report_meta_path(kind: ReportKind, trade_date: date) -> Path:
    """Sidecar JSON holding title / metrics / created_at for the report."""
    return _reports_dir() / f"{kind.value}_{trade_date.isoformat()}.meta.json"


def _load_report_from_disk(kind: ReportKind, trade_date: date) -> MarketReport | None:
    """Reconstruct a MarketReport from the markdown + sidecar metadata.

    The markdown file is the source of truth for the body. The sidecar
    carries everything else (title, metrics, created_at) and may be
    missing for older reports — in that case we degrade gracefully.
    """
    path = _report_path(kind, trade_date)
    if not path.exists():
        return None
    import json as _json

    markdown = path.read_text(encoding="utf-8")
    title = f"{_kind_label(kind)} — {trade_date.isoformat()}"
    created_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    metrics: dict[str, Any] = {}

    meta_path = _report_meta_path(kind, trade_date)
    if meta_path.exists():
        try:
            meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            logger.warning("failed to read report sidecar %s: %s", meta_path, exc)
            meta = {}
        if isinstance(meta, dict):
            title = str(meta.get("title") or title)
            created_at = str(meta.get("created_at") or created_at)
            raw_metrics = meta.get("metrics")
            if isinstance(raw_metrics, dict):
                metrics = raw_metrics

    return MarketReport(
        kind=kind,
        trade_date=trade_date,
        title=title,
        markdown=markdown,
        metrics=metrics,
        created_at=created_at,
    )


def _kind_label(kind: ReportKind) -> str:
    from src.ashare.tasks.report_prompts import KIND_LABELS_BY_VALUE

    return KIND_LABELS_BY_VALUE.get(kind.value, kind.value)


def _fetch_index_quotes(symbols: list[str]) -> dict[str, dict[str, float]]:
    """Fetch multiple index quotes from tushare/adshare /market/snapshot."""
    if not symbols:
        return {}
    try:
        client = TushareClient()
        try:
            payload = client.get_snapshot(symbols)
            data_list = payload.get("data", []) if isinstance(payload, dict) else []
            result: dict[str, dict[str, float]] = {}
            for item in data_list:
                code = item.get("code") or item.get("symbol")
                if not code:
                    continue
                result[str(code)] = {
                    "price": float(item.get("last", 0) or item.get("close", 0) or 0),
                    "change_pct": float(item.get("changePct", 0) or item.get("change_pct", 0) or 0),
                    "open": float(item.get("open", 0) or 0),
                    "high": float(item.get("high", 0) or 0),
                    "low": float(item.get("low", 0) or 0),
                }
            return result
        finally:
            client.close()
    except Exception as exc:
        logger.warning("tushare index quotes failed for %s: %s", symbols, exc)
    return {}


def _dominant_source(records: list[LimitUpDaily]) -> str:
    """Return the most common source among records, defaulting to AkShare."""
    if not records:
        return "AkShare"
    counts: dict[str, int] = {}
    for r in records:
        counts[r.source] = counts.get(r.source, 0) + 1
    return max(counts, key=counts.get)  # type: ignore[arg-type]


def _broken_rate(records: list[LimitUpDaily]) -> float:
    """Ever-limit-up stocks that failed to seal at close / total ever-limit-up."""
    if not records:
        return 0.0
    return round(len([r for r in records if not r.is_sealed]) / len(records) * 100, 2)


def _board_height_distribution(records: list[LimitUpDaily]) -> dict[str, int]:
    """Count sealed records by consecutive limit-up height."""
    sealed = [r for r in records if r.is_sealed]
    return {
        "first": sum(1 for r in sealed if r.limit_up_count == 1),
        "second": sum(1 for r in sealed if r.limit_up_count == 2),
        "third": sum(1 for r in sealed if r.limit_up_count == 3),
        "fourth_plus": sum(1 for r in sealed if r.limit_up_count >= 4),
    }


def _seal_tiers(records: list[LimitUpDaily]) -> dict[str, int]:
    """Classify sealed limit-up stocks by seal amount."""
    tiers: dict[str, int] = {"极强": 0, "强": 0, "中": 0, "弱": 0}
    for r in records:
        if not r.is_sealed:
            continue
        amount = r.seal_amount or 0
        if amount > 500_000_000:
            tiers["极强"] += 1
        elif amount > 200_000_000:
            tiers["强"] += 1
        elif amount > 50_000_000:
            tiers["中"] += 1
        else:
            tiers["弱"] += 1
    return tiers


def _open_rate_for_record(r: LimitUpDaily) -> float:
    """Open rate = open_count / (open_count + 1)."""
    opens = r.open_count or 0
    return round(opens / (opens + 1) * 100, 2)


def _best_record_per_symbol(records: list[LimitUpDaily]) -> list[LimitUpDaily]:
    """Deduplicate records by symbol, keeping the strongest one.

    For weekly aggregation the same symbol may appear on multiple days;
    keep the record with the highest limit_up_count then largest seal_amount.
    """
    best: dict[str, LimitUpDaily] = {}
    for r in records:
        existing = best.get(r.symbol)
        if existing is None:
            best[r.symbol] = r
            continue
        if r.limit_up_count > existing.limit_up_count:
            best[r.symbol] = r
        elif r.limit_up_count == existing.limit_up_count and (r.seal_amount or 0) > (existing.seal_amount or 0):
            best[r.symbol] = r
    return list(best.values())


def _strong_targets(records: list[LimitUpDaily]) -> list[dict[str, Any]]:
    """Strategy A: high consecutive boards + low open rate + large seal."""
    result = []
    for r in _best_record_per_symbol(records):
        if not r.is_sealed:
            continue
        if r.limit_up_count < 3:
            continue
        if _open_rate_for_record(r) >= 20:
            continue
        if (r.seal_amount or 0) < 200_000_000:
            continue
        result.append(
            {
                "symbol": r.symbol,
                "name": r.name,
                "limit_up_count": r.limit_up_count,
                "open_rate": _open_rate_for_record(r),
                "seal_amount": r.seal_amount or 0,
                "industry": r.industry or "",
                "concept": r.concept or "",
            }
        )
    return sorted(result, key=lambda x: (x["limit_up_count"], x["seal_amount"]), reverse=True)[:10]


def _first_breakout_targets(records: list[LimitUpDaily]) -> list[dict[str, Any]]:
    """Strategy B: first board + meaningful seal + has sector heat."""
    result = []
    for r in _best_record_per_symbol(records):
        if not r.is_sealed:
            continue
        if r.limit_up_count != 1:
            continue
        if (r.seal_amount or 0) < 50_000_000:
            continue
        result.append(
            {
                "symbol": r.symbol,
                "name": r.name,
                "seal_amount": r.seal_amount or 0,
                "industry": r.industry or "",
                "concept": r.concept or "",
            }
        )
    return sorted(result, key=lambda x: x["seal_amount"], reverse=True)[:10]


def _effective_seal_ratio(r: LimitUpDaily) -> float:
    """Return the best available seal ratio for a record.

    AkShare often leaves seal_ratio null but provides turnover_amount.
    Fall back to seal_amount / turnover_amount when the explicit ratio is missing.
    """
    if r.seal_ratio is not None and r.seal_ratio > 0:
        return r.seal_ratio
    turnover = r.turnover_amount or 0
    if turnover > 0:
        return (r.seal_amount or 0) / turnover
    return 0.0


def _aggregate_tags(records: list[LimitUpDaily], getter) -> list[dict[str, Any]]:
    """Aggregate counts and seal amounts by a comma-separated tag field."""
    groups: dict[str, dict[str, Any]] = {}
    for r in records:
        raw = getter(r)
        if not raw:
            continue
        for tag in str(raw).split(","):
            tag = tag.strip()
            if not tag:
                continue
            entry = groups.setdefault(
                tag,
                {
                    "name": tag,
                    "count": 0,
                    "seal_amount": 0.0,
                    "limit_up_count_sum": 0,
                    "seal_ratio_sum": 0.0,
                    "records": 0,
                },
            )
            entry["count"] += 1
            entry["seal_amount"] += r.seal_amount or 0
            entry["limit_up_count_sum"] += r.limit_up_count
            entry["seal_ratio_sum"] += _effective_seal_ratio(r)
            entry["records"] += 1

    for entry in groups.values():
        n = max(entry.pop("records"), 1)
        entry["avg_limit_up_count"] = round(entry.pop("limit_up_count_sum") / n, 2)
        entry["avg_seal_ratio"] = round(entry.pop("seal_ratio_sum") / n, 4)

    return sorted(groups.values(), key=lambda x: (x["count"], x["seal_amount"]), reverse=True)[:10]


def _collect_metrics(trade_date: date, store: LimitUpStore) -> MarketMetrics:
    """Collect raw metrics for report generation."""
    records = list(store.load_day(trade_date).values())
    if not records:
        return MarketMetrics(
            trade_date=trade_date,
            data_source="AkShare",
        )

    sealed = [r for r in records if r.is_sealed]
    broken = [r for r in records if not r.is_sealed]
    max_count = max((r.limit_up_count for r in records), default=0)
    leader = max(records, key=lambda r: (r.limit_up_count, r.seal_amount or 0))
    distribution = _board_height_distribution(records)

    hot_concepts = _aggregate_tags(records, lambda r: r.concept)
    hot_industries = _aggregate_tags(records, lambda r: r.industry)
    data_source = _dominant_source(records)

    return MarketMetrics(
        trade_date=trade_date,
        limit_up_count=len(sealed),
        limit_up_opened_count=len(broken),
        max_limit_up_count=max_count,
        total_seal_amount=sum((r.seal_amount or 0) for r in sealed),
        leading_symbol=leader.symbol,
        leading_name=leader.name,
        index_quotes=_fetch_index_quotes(_INDEX_SYMBOLS),
        hot_concepts=hot_concepts,
        hot_industries=hot_industries,
        data_source=data_source,
        first_board_count=distribution["first"],
        second_board_count=distribution["second"],
        third_board_count=distribution["third"],
        fourth_plus_board_count=distribution["fourth_plus"],
        ever_limit_up_count=len(records),
        broken_rate=_broken_rate(records),
        seal_tiers=_seal_tiers(records),
        strong_targets=_strong_targets(records),
        first_breakout_targets=_first_breakout_targets(records),
    )


def _collect_weekly_metrics(week_end: date, store: LimitUpStore) -> MarketMetrics:
    """Aggregate metrics across the whole trading week ending on week_end."""
    week_start = week_end - timedelta(days=week_end.weekday())
    daily_records = store.load_range(week_start, week_end)

    all_records: list[LimitUpDaily] = []
    daily_limit_up_counts: dict[str, int] = {}
    for day, symbol_map in daily_records.items():
        day_records = list(symbol_map.values())
        all_records.extend(day_records)
        daily_limit_up_counts[day.isoformat()] = len([r for r in day_records if r.is_sealed])

    if not all_records:
        metrics = _collect_metrics(week_end, store)
        metrics.daily_limit_up_counts = daily_limit_up_counts
        metrics.weekly_start = week_start.isoformat()
        metrics.weekly_end = week_end.isoformat()
        return metrics

    sealed = [r for r in all_records if r.is_sealed]
    broken = [r for r in all_records if not r.is_sealed]
    max_count = max((r.limit_up_count for r in all_records), default=0)
    leader = max(all_records, key=lambda r: (r.limit_up_count, r.seal_amount or 0))
    distribution = _board_height_distribution(all_records)

    metrics = MarketMetrics(
        trade_date=week_end,
        limit_up_count=len(sealed),
        limit_up_opened_count=len(broken),
        max_limit_up_count=max_count,
        total_seal_amount=sum((r.seal_amount or 0) for r in sealed),
        leading_symbol=leader.symbol,
        leading_name=leader.name,
        index_quotes=_fetch_index_quotes(_INDEX_SYMBOLS),
        hot_concepts=_aggregate_tags(all_records, lambda r: r.concept),
        hot_industries=_aggregate_tags(all_records, lambda r: r.industry),
        data_source=_dominant_source(all_records),
        first_board_count=distribution["first"],
        second_board_count=distribution["second"],
        third_board_count=distribution["third"],
        fourth_plus_board_count=distribution["fourth_plus"],
        ever_limit_up_count=len(all_records),
        broken_rate=_broken_rate(all_records),
        seal_tiers=_seal_tiers(all_records),
        strong_targets=_strong_targets(all_records),
        first_breakout_targets=_first_breakout_targets(all_records),
        daily_limit_up_counts=daily_limit_up_counts,
        weekly_start=week_start.isoformat(),
        weekly_end=week_end.isoformat(),
    )
    return metrics


def _format_index_table(quotes: dict[str, dict[str, float]]) -> list[str]:
    """Render a markdown table for index quotes."""
    lines = [
        "## 主要指数行情",
        "",
        "| 指数 | 最新价 | 涨跌幅 | 开盘价 | 最高价 | 最低价 |",
        "|------|--------|--------|--------|--------|--------|",
    ]
    for code in _INDEX_SYMBOLS:
        name = _INDEX_NAMES.get(code, code)
        q = quotes.get(code, {})
        if not q:
            lines.append(f"| {name} | - | - | - | - | - |")
            continue
        lines.append(
            f"| {name} | {q.get('price', 0):.2f} | "
            f"{q.get('change_pct', 0):+.2f}% | {q.get('open', 0):.2f} | "
            f"{q.get('high', 0):.2f} | {q.get('low', 0):.2f} |"
        )
    lines.append("")
    return lines


def _format_limit_up_overview(metrics: MarketMetrics) -> list[str]:
    """Render the limit-up overview section."""
    return [
        "## 涨停概览",
        "",
        f"- 涨停家数: {metrics.limit_up_count}",
        f"- 炸板家数: {metrics.limit_up_opened_count}",
        f"- 曾触板家数: {metrics.ever_limit_up_count}",
        f"- 炸板率: {metrics.broken_rate}%",
        f"- 最高连板: {metrics.max_limit_up_count} 板",
        f"- 总封单金额: ¥{metrics.total_seal_amount:,.0f}",
        "",
        "### 连板高度分布",
        "",
        f"- 首板: {metrics.first_board_count} 只",
        f"- 2 连板: {metrics.second_board_count} 只",
        f"- 3 连板: {metrics.third_board_count} 只",
        f"- 4 连板及以上: {metrics.fourth_plus_board_count} 只",
        "",
    ]


def _format_seal_tiers(tiers: dict[str, int]) -> list[str]:
    """Render seal-amount tier distribution."""
    lines = [
        "### 封单强度分布",
        "",
        "| 强度 | 家数 | 说明 |",
        "|------|------|------|",
    ]
    desc = {
        "极强": "> 5 亿",
        "强": "2 - 5 亿",
        "中": "5000 万 - 2 亿",
        "弱": "< 5000 万",
    }
    for tier in ("极强", "强", "中", "弱"):
        lines.append(f"| {tier} | {tiers.get(tier, 0)} | {desc[tier]} |")
    lines.append("")
    return lines


def _format_target_table(title: str, rows: list[dict[str, Any]]) -> list[str]:
    """Render strong or first-breakout target table."""
    if not rows:
        return []
    lines = [f"## {title}", "", "| 代码 | 名称 | 连板 | 炸板率 | 封单金额 | 所属板块 |", "|------|------|------|--------|----------|----------|"]
    for r in rows:
        tag = r.get("concept") or r.get("industry") or "-"
        lines.append(
            f"| {r['symbol']} | {r['name']} | {r.get('limit_up_count', '-')} | "
            f"{r.get('open_rate', '-')} | ¥{r['seal_amount']:,.0f} | {tag} |"
        )
    lines.append("")
    return lines


def _format_first_breakout_table(rows: list[dict[str, Any]]) -> list[str]:
    """Render first-breakout target table."""
    if not rows:
        return []
    lines = ["## 首板机会", "", "| 代码 | 名称 | 封单金额 | 所属板块 |", "|------|------|----------|----------|"]
    for r in rows:
        tag = r.get("concept") or r.get("industry") or "-"
        lines.append(f"| {r['symbol']} | {r['name']} | ¥{r['seal_amount']:,.0f} | {tag} |")
    lines.append("")
    return lines


def _format_tag_table(title: str, rows: list[dict[str, Any]]) -> list[str]:
    """Render a markdown table for concept/industry groupings."""
    if not rows:
        return []
    lines = [
        f"## {title}",
        "",
        "| 名称 | 涨停家数 | 封单金额 | 平均连板 | 平均封单比 |",
        "|------|----------|----------|----------|------------|",
    ]
    for c in rows:
        lines.append(
            f"| {c['name']} | {c['count']} | ¥{c['seal_amount']:,.0f} | "
            f"{c.get('avg_limit_up_count', 0):.2f} | {c.get('avg_seal_ratio', 0):.2%} |"
        )
    lines.append("")
    return lines


def _format_leader(metrics: MarketMetrics) -> list[str]:
    """Render leader tracking section."""
    return [
        "## 龙头追踪",
        "",
        f"- {metrics.leading_symbol} {metrics.leading_name} — {metrics.max_limit_up_count} 连板",
        "",
    ]


def _render_data_appendix(metrics: MarketMetrics, extra_sections: list[str] | None = None) -> str:
    """Render the data-driven appendix used in all report kinds."""
    lines: list[str] = []
    lines.extend(_format_index_table(metrics.index_quotes))
    lines.extend(_format_limit_up_overview(metrics))
    lines.extend(_format_seal_tiers(metrics.seal_tiers))
    lines.extend(_format_leader(metrics))
    lines.extend(_format_target_table("强势标的（连板策略）", metrics.strong_targets))
    lines.extend(_format_first_breakout_table(metrics.first_breakout_targets))
    lines.extend(_format_tag_table("热门概念 TOP10", metrics.hot_concepts))
    lines.extend(_format_tag_table("热门行业 TOP10", metrics.hot_industries))
    if extra_sections:
        lines.extend(extra_sections)
    lines.append(f"*数据来源: {metrics.data_source} / Vibe-Trading A-share extension*")
    lines.append("")
    return "\n".join(lines)


def _render_open_report(metrics: MarketMetrics, analysis: str) -> str:
    title = f"# A股开盘报告 — {metrics.trade_date.isoformat()}"
    return "\n\n".join([title, analysis, _render_data_appendix(metrics)])


def _render_close_report(metrics: MarketMetrics, analysis: str) -> str:
    title = f"# A股收盘复盘 — {metrics.trade_date.isoformat()}"
    return "\n\n".join([title, analysis, _render_data_appendix(metrics)])


def _render_weekly_report(metrics: MarketMetrics, week_start: date, week_end: date, analysis: str) -> str:
    title = f"# A股周度复盘 — {week_start.isoformat()} ~ {week_end.isoformat()}"
    extra: list[str] = []
    if metrics.daily_limit_up_counts:
        extra = ["## 本周涨停家数趋势", "", "| 日期 | 涨停家数 |", "|------|----------|"]
        for day in sorted(metrics.daily_limit_up_counts):
            extra.append(f"| {day} | {metrics.daily_limit_up_counts[day]} |")
        extra.append("")
    return "\n\n".join([title, analysis, _render_data_appendix(metrics, extra)])


def _previous_trade_date(trade_date: date, store: LimitUpStore) -> date:
    """Find the most recent trading date before trade_date that has data."""
    candidate = trade_date - timedelta(days=1)
    for _ in range(30):
        if store.load_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    # Fallback to calendar yesterday if no stored data found.
    return trade_date - timedelta(days=1)


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

        week_start: date | None = None
        week_end: date | None = None

        if kind == ReportKind.OPEN:
            # Open report is based on the previous trading day's close data.
            data_date = _previous_trade_date(trade_date, self.store)
            metrics = _collect_metrics(data_date, self.store)
            title = f"A股开盘报告 — {trade_date.isoformat()}"
        elif kind == ReportKind.CLOSE:
            metrics = _collect_metrics(trade_date, self.store)
            title = f"A股收盘复盘 — {trade_date.isoformat()}"
        elif kind == ReportKind.WEEKLY:
            week_end = trade_date
            week_start = week_end - timedelta(days=week_end.weekday())
            metrics = _collect_weekly_metrics(week_end, self.store)
            title = f"A股周度复盘 — {week_start.isoformat()} ~ {week_end.isoformat()}"
        else:
            raise ValueError(f"unknown report kind: {kind}")

        metrics_dict = metrics.to_dict()
        analysis, llm_meta = await self._generate_analysis(kind, metrics_dict, trade_date, week_start, week_end)

        if kind == ReportKind.OPEN:
            markdown = _render_open_report(metrics, analysis)
        elif kind == ReportKind.CLOSE:
            markdown = _render_close_report(metrics, analysis)
        else:
            markdown = _render_weekly_report(metrics, week_start, week_end, analysis)

        report = MarketReport(
            kind=kind,
            trade_date=trade_date,
            title=title,
            markdown=markdown,
            metrics={**metrics_dict, "llm": llm_meta},
            created_at=datetime.utcnow().isoformat(),
        )

        path = _report_path(kind, trade_date)
        path.write_text(markdown, encoding="utf-8")
        meta_path = _report_meta_path(kind, trade_date)
        meta_path.write_text(
            json.dumps(
                {
                    "title": report.title,
                    "created_at": report.created_at,
                    "metrics": report.metrics,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        logger.info("wrote %s report to %s", kind.value, path)
        return report

    async def _generate_analysis(
        self,
        kind: ReportKind,
        metrics: dict[str, Any],
        trade_date: date,
        week_start: date | None = None,
        week_end: date | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Generate LLM analysis, falling back to a data-only notice on failure."""
        try:
            result = await generate_report_analysis(kind, metrics, trade_date, week_start, week_end)
            analysis = result.markdown
            llm_meta = {
                "model": result.model,
                "duration_seconds": round(result.duration_seconds, 3),
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "used": True,
            }
        except ReportLLMError as exc:
            logger.warning("falling back to data-only report for %s: %s", kind.value, exc)
            analysis = (
                "## 摘要\n\n"
                "- 当前未配置 LLM 或模型调用失败，本报告仅展示结构化市场数据，不包含 AI 分析观点。\n"
                "- 配置 LANGCHAIN_MODEL_NAME 后可自动生成摘要、核心观点、情绪研判与操作建议。\n"
            )
            llm_meta = {"used": False, "error": str(exc)}
        except Exception as exc:
            logger.warning("unexpected LLM failure for %s: %s", kind.value, exc)
            analysis = (
                "## 摘要\n\n"
                "- 当前未配置 LLM 或模型调用失败，本报告仅展示结构化市场数据，不包含 AI 分析观点。\n"
                "- 配置 LANGCHAIN_MODEL_NAME 后可自动生成摘要、核心观点、情绪研判与操作建议。\n"
            )
            llm_meta = {"used": False, "error": str(exc)}
        return analysis, llm_meta
