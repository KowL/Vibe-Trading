"""A-share mandate management tool for the agent.

Provides create/list/check operations for A-share trading mandates.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from src.agent.tools import BaseTool
from src.ashare.trading import AShareMandateConfig, create_default_ashare_mandate


# In-memory mandate store (persisted to file in production)
_mandate_store: dict[str, AShareMandateConfig] = {}


class AShareMandateTool(BaseTool):
    """Manage A-share trading mandates (authorization for live trading)."""

    name = "ashare_mandate"
    description = (
        "管理A股实盘交易授权书(Mandate)。可以创建、列出、检查授权状态。"
        "所有实盘交易必须通过授权书检查。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "check", "revoke"],
                "description": "操作类型",
            },
            "mandate_id": {
                "type": "string",
                "description": "授权书ID (create时不需)",
            },
            "broker": {
                "type": "string",
                "description": "券商代码 (create时有效)",
            },
            "account_ref": {
                "type": "string",
                "description": "账户标识 (create时有效)",
            },
            "max_order_cny": {
                "type": "number",
                "description": "单笔最大金额CNY (create时有效)",
            },
            "max_exposure_cny": {
                "type": "number",
                "description": "最大总敞口CNY (create时有效)",
            },
        },
        "required": ["action"],
    }
    repeatable = True
    is_readonly = False

    def execute(
        self,
        action: str,
        mandate_id: str = "",
        broker: str = "simulated",
        account_ref: str = "paper_001",
        max_order_cny: float = 100_000.0,
        max_exposure_cny: float = 1_000_000.0,
        **kwargs: Any,
    ) -> str:
        try:
            if action == "create":
                mid = f"ashare_mandate_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                mandate = AShareMandateConfig(
                    broker=broker,
                    account_ref=account_ref,
                    max_order_notional_cny=max_order_cny,
                    max_total_exposure_cny=max_exposure_cny,
                )
                _mandate_store[mid] = mandate
                return json.dumps(
                    {
                        "status": "success",
                        "mandate_id": mid,
                        "mandate": mandate.to_dict(),
                        "expires_at": (datetime.now() + timedelta(days=30)).isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )

            elif action == "list":
                return json.dumps(
                    {
                        "status": "success",
                        "count": len(_mandate_store),
                        "mandates": [
                            {"id": k, **v.to_dict()} for k, v in _mandate_store.items()
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )

            elif action == "check":
                if not mandate_id or mandate_id not in _mandate_store:
                    return json.dumps(
                        {"status": "error", "error": f"Mandate {mandate_id} not found"},
                        ensure_ascii=False,
                    )
                m = _mandate_store[mandate_id]
                return json.dumps(
                    {
                        "status": "success",
                        "mandate_id": mandate_id,
                        "valid": True,
                        "mandate": m.to_dict(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )

            elif action == "revoke":
                if mandate_id in _mandate_store:
                    del _mandate_store[mandate_id]
                    return json.dumps(
                        {"status": "success", "message": f"Mandate {mandate_id} revoked"},
                        ensure_ascii=False,
                    )
                return json.dumps(
                    {"status": "error", "error": f"Mandate {mandate_id} not found"},
                    ensure_ascii=False,
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


class AShareTradeTool(BaseTool):
    """Execute A-share trades (Mandate-gated)."""

    name = "ashare_trade"
    description = (
        "执行A股交易（买入/卖出）。必须先创建Mandate授权书，"
        "交易会自动检查授权限制（金额、数量、ST排除等）。"
        "当前仅支持模拟交易。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "mandate_id": {
                "type": "string",
                "description": "授权书ID",
            },
            "symbol": {
                "type": "string",
                "description": "股票代码，如 600403.SH",
            },
            "side": {
                "type": "string",
                "enum": ["buy", "sell"],
                "description": "交易方向",
            },
            "quantity": {
                "type": "integer",
                "description": "交易数量（股）",
            },
            "price": {
                "type": "number",
                "description": "委托价格",
            },
        },
        "required": ["mandate_id", "symbol", "side", "quantity", "price"],
    }
    repeatable = False
    is_readonly = False

    def execute(
        self,
        mandate_id: str,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        **kwargs: Any,
    ) -> str:
        # Check mandate exists
        if mandate_id not in _mandate_store:
            return json.dumps(
                {"status": "error", "error": f"Mandate {mandate_id} not found. Create one first."},
                ensure_ascii=False,
            )

        mandate = _mandate_store[mandate_id]

        # Validate against mandate
        notional = quantity * price
        if notional > mandate.max_order_notional_cny:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"Order notional ¥{notional:,.2f} exceeds mandate limit ¥{mandate.max_order_notional_cny:,.2f}",
                },
                ensure_ascii=False,
            )

        if mandate.exclude_st and ("ST" in symbol or "*ST" in symbol):
            return json.dumps(
                {"status": "error", "error": "ST/*ST stocks are excluded by mandate"},
                ensure_ascii=False,
            )

        if symbol in mandate.exclude_symbols:
            return json.dumps(
                {"status": "error", "error": f"Symbol {symbol} is in denylist"},
                ensure_ascii=False,
            )

        # Simulate trade execution
        order_id = f"ashare_order_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{symbol.split('.')[0]}"

        return json.dumps(
            {
                "status": "success",
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": price,
                "notional": notional,
                "mandate_id": mandate_id,
                "broker": mandate.broker,
                "simulated": mandate.broker == "simulated",
                "timestamp": datetime.now().isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
