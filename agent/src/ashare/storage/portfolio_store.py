"""Persistence for Portfolio / Trade records.

Layout:
    ~/.vibe-trading/ashare/portfolios/<portfolio_id>.json   Portfolio
    ~/.vibe-trading/ashare/portfolios/<portfolio_id>/trades.jsonl   Trades
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Iterable

from src.ashare.models.portfolio import Portfolio, Trade

logger = logging.getLogger(__name__)

_PORTFOLIO_SUBDIR = "ashare/portfolios"


def _portfolio_root() -> Path:
    root = Path.home() / ".vibe-trading" / _PORTFOLIO_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    return root


class PortfolioStore:
    """File-system persistence for paper portfolios and their trades."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else _portfolio_root()

    def _portfolio_path(self, portfolio_id: str) -> Path:
        return self.root / f"{portfolio_id}.json"

    def _trades_path(self, portfolio_id: str) -> Path:
        trades_dir = self.root / portfolio_id
        trades_dir.mkdir(parents=True, exist_ok=True)
        return trades_dir / "trades.jsonl"

    def new_portfolio_id(self) -> str:
        return f"ashare_pf_{uuid.uuid4().hex[:8]}"

    def save_portfolio(self, portfolio: Portfolio) -> Path:
        path = self._portfolio_path(portfolio.portfolio_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(portfolio.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def load_portfolio(self, portfolio_id: str) -> Portfolio:
        path = self._portfolio_path(portfolio_id)
        if not path.exists():
            raise FileNotFoundError(f"Portfolio not found: {portfolio_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return Portfolio.from_dict(data)

    def list_portfolios(self) -> list[Portfolio]:
        portfolios: list[Portfolio] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                portfolios.append(Portfolio.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, OSError, KeyError):
                logger.warning("skipping malformed portfolio file: %s", path)
                continue
        return portfolios

    def save_trades(self, portfolio_id: str, trades: Iterable[Trade]) -> Path:
        path = self._trades_path(portfolio_id)
        lines = [t.to_dict() for t in trades]
        path.write_text(
            "\n".join(json.dumps(line, ensure_ascii=False) for line in lines)
            + ("\n" if lines else ""),
            encoding="utf-8",
        )
        return path

    def load_trades(self, portfolio_id: str) -> list[Trade]:
        path = self._trades_path(portfolio_id)
        if not path.exists():
            return []
        trades: list[Trade] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                trades.append(Trade.from_dict(json.loads(line)))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                logger.warning("skipping malformed trade record: %s", line[:200])
                continue
        return trades

    def append_trade(self, portfolio_id: str, trade: Trade) -> Path:
        path = self._trades_path(portfolio_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(trade.to_dict(), ensure_ascii=False) + "\n")
        return path
