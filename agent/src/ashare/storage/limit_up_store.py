"""Persistence for LimitUpDaily records.

Layout:
    ~/.vibe-trading/ashare/limit_up/<YYYY>/<YYYYMMDD>.jsonl
One line per symbol, keyed by symbol for idempotent writes.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Iterable

from src.ashare.models.limit_up import LimitUpDaily

logger = logging.getLogger(__name__)

_LIMIT_UP_SUBDIR = "ashare/limit_up"


def _limit_up_root() -> Path:
    root = Path.home() / ".vibe-trading" / _LIMIT_UP_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _day_path(trade_date: date) -> Path:
    root = _limit_up_root()
    return root / str(trade_date.year) / f"{trade_date.strftime('%Y%m%d')}.jsonl"


class LimitUpStore:
    """Crash-safe JSONL store for daily limit-up data."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else _limit_up_root()

    def save(self, records: Iterable[LimitUpDaily]) -> Path:
        """Persist records grouped by trade_date, overwriting existing days."""
        by_day: dict[date, dict[str, LimitUpDaily]] = {}
        for rec in records:
            by_day.setdefault(rec.trade_date, {})[rec.symbol] = rec

        written: Path | None = None
        for day, symbol_map in by_day.items():
            path = _day_path(day)
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = [rec.to_dict() for rec in symbol_map.values()]
            path.write_text(
                "\n".join(json.dumps(line, ensure_ascii=False) for line in lines)
                + ("\n" if lines else ""),
                encoding="utf-8",
            )
            written = path
            logger.info("wrote %d limit-up records to %s", len(lines), path)
        if written is None:
            raise ValueError("no records to save")
        return written

    def load_day(self, trade_date: date) -> dict[str, LimitUpDaily]:
        """Load all records for a single trading day keyed by symbol."""
        path = _day_path(trade_date)
        if not path.exists():
            return {}
        result: dict[str, LimitUpDaily] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = LimitUpDaily.from_dict(json.loads(line))
                result[rec.symbol] = rec
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                logger.warning("skipping malformed limit-up record: %s", line[:200])
                continue
        return result

    def load_range(
        self, start: date, end: date
    ) -> dict[date, dict[str, LimitUpDaily]]:
        """Load records across a date range."""
        result: dict[date, dict[str, LimitUpDaily]] = {}
        for year_dir in self.root.iterdir():
            if not year_dir.is_dir():
                continue
            for path in year_dir.glob("*.jsonl"):
                try:
                    day = date(int(path.stem[:4]), int(path.stem[4:6]), int(path.stem[6:8]))
                except ValueError:
                    continue
                if start <= day <= end:
                    result[day] = self.load_day(day)
        return result

    def get(self, trade_date: date, symbol: str) -> LimitUpDaily | None:
        """Load a single record."""
        return self.load_day(trade_date).get(symbol)

    def latest_trade_date(self) -> date | None:
        """Return the most recent trade date with stored data."""
        latest: date | None = None
        for year_dir in self.root.iterdir():
            if not year_dir.is_dir():
                continue
            for path in year_dir.glob("*.jsonl"):
                try:
                    day = date(int(path.stem[:4]), int(path.stem[4:6]), int(path.stem[6:8]))
                except ValueError:
                    continue
                if latest is None or day > latest:
                    latest = day
        return latest
