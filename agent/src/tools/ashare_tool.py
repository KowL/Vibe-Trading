"""A-share market tools for the agent.

Provides limit-up data, portfolio management, and market reports.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any

import requests

from src.agent.tools import BaseTool


def _api_base() -> str:
    """Return the API base URL."""
    return os.getenv("VIBE_API_URL", "http://127.0.0.1:8899")


def _get(path: str) -> Any:
    res = requests.get(f"{_api_base()}{path}", timeout=30)
    res.raise_for_status()
    return res.json()


def _post(path: str, json_body: dict | None = None, params: dict | None = None) -> Any:
    res = requests.post(f"{_api_base()}{path}", json=json_body, params=params, timeout=60)
    res.raise_for_status()
    return res.json()


class AShareLimitUpTool(BaseTool):
    """Get A-share limit-up (涨停) records for a specific date."""

    name = "ashare_limit_up"
    description = (
        "获取A股涨停梯队数据。返回指定日期的涨停股票列表，"
        "包含连板高度、涨停价、封单金额、炸板状态等信息。"
        "适用于短线打板策略分析。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "trade_date": {
                "type": "string",
                "description": "交易日期，格式 YYYY-MM-DD，默认为今天",
            },
        },
        "required": [],
    }
    repeatable = True
    is_readonly = True

    def execute(self, trade_date: str = "", **kwargs: Any) -> str:
        if not trade_date:
            trade_date = date.today().isoformat()
        try:
            records = _get(f"/ashare/limit-up/{trade_date}")
            return json.dumps(
                {
                    "status": "success",
                    "trade_date": trade_date,
                    "count": len(records),
                    "records": records[:50],  # Limit to top 50
                },
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            return json.dumps(
                {"status": "error", "error": str(exc)}, ensure_ascii=False
            )


class AShareSyncLimitUpTool(BaseTool):
    """Sync A-share limit-up data from AmazingData."""

    name = "ashare_sync_limit_up"
    description = (
        "从AmazingData同步A股涨停数据。用于获取最新涨停信息，"
        "会先调用数据源API获取原始数据，再持久化到本地存储。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "trade_date": {
                "type": "string",
                "description": "交易日期，格式 YYYY-MM-DD，默认为今天",
            },
        },
        "required": [],
    }
    repeatable = False
    is_readonly = False

    def execute(self, trade_date: str = "", **kwargs: Any) -> str:
        if not trade_date:
            trade_date = date.today().isoformat()
        try:
            result = _post("/ashare/limit-up/sync", params={"trade_date": trade_date})
            return json.dumps(
                {
                    "status": "success",
                    "trade_date": trade_date,
                    "synced_count": result.get("count", 0),
                    "source": result.get("source", "unknown"),
                    "errors": result.get("errors", []),
                },
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            return json.dumps(
                {"status": "error", "error": str(exc)}, ensure_ascii=False
            )


class ASharePortfolioTool(BaseTool):
    """List or create A-share paper portfolios."""

    name = "ashare_portfolio"
    description = (
        "管理A股模拟持仓账户。可以列出所有账户或创建新账户。"
        "每个账户有独立的现金和交易记录。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "create"],
                "description": "操作类型：list=列出账户，create=创建账户",
            },
            "name": {
                "type": "string",
                "description": "创建账户时的名称（action=create时有效）",
            },
            "initial_cash": {
                "type": "number",
                "description": "初始资金，默认30万（action=create时有效）",
            },
        },
        "required": ["action"],
    }
    repeatable = True
    is_readonly = False

    def execute(
        self,
        action: str,
        name: str = "A股模拟账户",
        initial_cash: float = 300_000.0,
        **kwargs: Any,
    ) -> str:
        try:
            if action == "list":
                portfolios = _get("/ashare/portfolios")
                return json.dumps(
                    {
                        "status": "success",
                        "count": len(portfolios),
                        "portfolios": portfolios,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            elif action == "create":
                result = _post(
                    "/ashare/portfolios",
                    json_body={"name": name, "initial_cash": initial_cash},
                )
                return json.dumps(
                    {
                        "status": "success",
                        "portfolio_id": result.get("portfolio_id"),
                        "name": result.get("name"),
                        "initial_cash": result.get("initial_cash"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            else:
                return json.dumps(
                    {"status": "error", "error": f"Unknown action: {action}"},
                    ensure_ascii=False,
                )
        except Exception as exc:
            return json.dumps(
                {"status": "error", "error": str(exc)}, ensure_ascii=False
            )


class AShareReportTool(BaseTool):
    """Generate or fetch A-share market reports."""

    name = "ashare_report"
    description = (
        "获取A股市场报告。支持开盘报告、收盘复盘、周度复盘。"
        "可以生成新报告或读取已生成的报告。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["open", "close", "weekly"],
                "description": "报告类型：open=开盘报告，close=收盘复盘，weekly=周度复盘",
            },
            "trade_date": {
                "type": "string",
                "description": "交易日期，格式 YYYY-MM-DD，默认为今天",
            },
            "generate": {
                "type": "boolean",
                "description": "是否生成新报告，默认false只读取",
            },
        },
        "required": ["kind"],
    }
    repeatable = True
    is_readonly = False

    def execute(
        self,
        kind: str,
        trade_date: str = "",
        generate: bool = False,
        **kwargs: Any,
    ) -> str:
        if not trade_date:
            trade_date = date.today().isoformat()
        try:
            if generate:
                result = _post(
                    f"/ashare/reports/{kind}", params={"trade_date": trade_date}
                )
                return json.dumps(
                    {
                        "status": "success",
                        "kind": kind,
                        "trade_date": trade_date,
                        "title": result.get("title"),
                        "generated": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            else:
                report = _get(f"/ashare/reports/{kind}/{trade_date}")
                return json.dumps(
                    {
                        "status": "success",
                        "kind": kind,
                        "trade_date": trade_date,
                        "markdown": report.get("markdown", ""),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as exc:
            return json.dumps(
                {"status": "error", "error": str(exc)}, ensure_ascii=False
            )
