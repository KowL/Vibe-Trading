"""A-share live trading support (Mandate-gated).

This module provides A-share specific trading profiles and mandate
integration for Chinese brokerages (simulated layer for now).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class AShareBroker(str, Enum):
    """Supported A-share brokers."""

    SIMULATED = "simulated"  # Paper trading for testing
    THS = "ths"  # 同花顺 (future)
    EASTMONEY = "eastmoney"  # 东方财富 (future)
    FUTU = "futu"  # 富途 (HK-listed A-shares)
    LONGBRIDGE = "longbridge"  # 长桥 (HK-listed A-shares)


@dataclass(frozen=True)
class AShareMandateConfig:
    """A-share specific mandate configuration.

    Attributes:
        broker: Broker key
        account_ref: Account identifier
        max_order_notional_cny: Max single order in CNY
        max_total_exposure_cny: Max total exposure in CNY
        max_trades_per_day: Daily trade limit
        allowed_markets: e.g. ("sh", "sz", "bj")
        allowed_boards: e.g. ("main", "gem", "star")
        exclude_st: Whether to exclude ST/*ST stocks
        exclude_symbols: Hard denylist
    """

    broker: str
    account_ref: str
    max_order_notional_cny: float = 100_000.0
    max_total_exposure_cny: float = 1_000_000.0
    max_trades_per_day: int = 10
    allowed_markets: tuple[str, ...] = ("sh", "sz")
    allowed_boards: tuple[str, ...] = ("main", "gem", "star")
    exclude_st: bool = True
    exclude_symbols: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "broker": self.broker,
            "account_ref": self.account_ref,
            "max_order_notional_cny": self.max_order_notional_cny,
            "max_total_exposure_cny": self.max_total_exposure_cny,
            "max_trades_per_day": self.max_trades_per_day,
            "allowed_markets": list(self.allowed_markets),
            "allowed_boards": list(self.allowed_boards),
            "exclude_st": self.exclude_st,
            "exclude_symbols": list(self.exclude_symbols),
        }


def create_default_ashare_mandate(
    broker: str = "simulated",
    account_ref: str = "paper_001",
) -> AShareMandateConfig:
    """Create a default A-share mandate for paper trading."""
    return AShareMandateConfig(
        broker=broker,
        account_ref=account_ref,
        max_order_notional_cny=100_000.0,
        max_total_exposure_cny=1_000_000.0,
        max_trades_per_day=10,
    )
