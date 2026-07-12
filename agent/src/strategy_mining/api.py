"""REST API for strategy-mining artifacts.

Serves the JSON files produced by ``vibe-trading strategy {mine,search,race}``
so the frontend can browse, inspect and visualise them without reading the
user's home directory directly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/ashare/strategy-mining", tags=["strategy-mining"])


def _strategies_dir() -> Path:
    raw = os.getenv("VIBE_TRADING_STRATEGIES_DIR", "")
    if raw:
        return Path(raw)
    return Path.home() / ".vibe-trading" / "strategies"


def _artifact_path(artifact_id: str, kind: str) -> Path | None:
    """Resolve an artifact file by timestamp id and kind."""
    d = _strategies_dir()
    filenames = {
        "config": f"strategy_{artifact_id}.json",
        "report": f"strategy_report_{artifact_id}.json",
        "search": f"strategy_search_{artifact_id}.json",
        "race": f"strategy_race_{artifact_id}.json",
    }
    filename = filenames.get(kind)
    if not filename:
        return None
    path = d / filename
    return path if path.is_file() else None


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@router.get("/list")
def list_strategy_artifacts() -> list[dict[str, Any]]:
    """List available strategy artifacts, newest first.

    Each entry summarises a single ``strategy_<timestamp>`` run and links to its
    optional report, search and race companions.
    """
    d = _strategies_dir()
    if not d.is_dir():
        return []

    configs = sorted(d.glob("strategy_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict[str, Any]] = []

    for cfg_path in configs:
        stem = cfg_path.stem
        # Skip the companion files; we only want the root strategy_*.json entries.
        if stem.startswith("strategy_report_") or stem.startswith("strategy_search_") or stem.startswith("strategy_race_"):
            continue
        if not stem.startswith("strategy_"):
            continue

        artifact_id = stem[len("strategy_") :]
        cfg = _safe_read_json(cfg_path)
        if cfg is None:
            continue

        report_path = d / f"strategy_report_{artifact_id}.json"
        report = _safe_read_json(report_path) if report_path.is_file() else None

        out.append(
            {
                "id": artifact_id,
                "created_at": cfg.get("created_at", ""),
                "params": {
                    k: cfg.get(k)
                    for k in (
                        "universe",
                        "period",
                        "top_n",
                        "train_years",
                        "max_per_theme",
                        "use_market_filter",
                        "neutralize",
                        "neutralize_fields",
                        "replacement_buffer",
                    )
                },
                "metrics": report.get("metrics", {}) if report else {},
                "selected_alphas": cfg.get("selected_alphas", []),
                "hypothesis_id": cfg.get("hypothesis_id", ""),
                "has_report": report_path.is_file(),
                "has_search": (d / f"strategy_search_{artifact_id}.json").is_file(),
                "has_race": (d / f"strategy_race_{artifact_id}.json").is_file(),
            }
        )

    return out


@router.get("/artifact/{artifact_id}")
def get_artifact(
    artifact_id: str,
    kind: str = Query(default="report", enum=["config", "report", "search", "race"]),
) -> Any:
    """Return the full JSON content of a strategy artifact."""
    path = _artifact_path(artifact_id, kind)
    if path is None:
        raise HTTPException(status_code=404, detail=f"{kind} artifact not found for {artifact_id}")

    data = _safe_read_json(path)
    if data is None:
        raise HTTPException(status_code=500, detail=f"Failed to read {kind} artifact")
    return data


@router.get("/artifact/{artifact_id}/equity")
def get_artifact_equity(artifact_id: str) -> dict[str, Any]:
    """Return the reconstructed equity curve for a strategy report.

    The strategy-mining report stores portfolios per rebalance date but not the
    resulting equity curve.  We rebuild it here from the stored portfolios and
    the local price panel, using the same 5-day forward-return and turnover-cost
    assumptions as the miner.
    """
    import numpy as np
    import pandas as pd

    from src.strategy_mining.miner import RollingICMiner
    from src.tools.alpha_bench_tool import _load_universe_panel

    path = _artifact_path(artifact_id, "report")
    if path is None:
        raise HTTPException(status_code=404, detail="report not found")

    report = _safe_read_json(path)
    if report is None:
        raise HTTPException(status_code=500, detail="Failed to read report")

    raw_portfolios = report.get("portfolios")
    if not raw_portfolios:
        return {"equity_curve": []}

    universe = report.get("universe", "csi300")
    period = report.get("period", "2020-2025")
    try:
        panel = _load_universe_panel(universe, period, use_cache=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load price panel: {exc}") from exc

    weekly_returns = RollingICMiner._compute_weekly_forward_returns(panel)

    # Parse rebalance dates and sort chronologically.
    portfolios: dict[pd.Timestamp, set[str]] = {
        pd.Timestamp(d): set(codes) for d, codes in raw_portfolios.items()
    }
    dates = sorted(portfolios.keys())

    one_side_cost = 0.0005  # 5 bps, matches the miner's default.
    equity = 1.0
    prev: set[str] = set()
    curve: list[dict[str, Any]] = []

    for d in dates:
        target = portfolios[d]
        week_bar = weekly_returns.loc[d].replace([np.inf, -np.inf], np.nan)

        if target:
            available = [c for c in target if c in week_bar.index]
            ret = float(week_bar[available].mean(skipna=True)) if available else 0.0
            top_n = len(target)
        else:
            ret = 0.0
            top_n = 0

        # Turnover cost: round-trip on changed names.
        if top_n:
            entered = target - prev
            exited = prev - target
            turnover = (len(entered) + len(exited)) / top_n
            ret -= turnover * 2.0 * one_side_cost

        equity *= 1.0 + ret
        curve.append({"date": d.strftime("%Y-%m-%d"), "equity": round(equity, 6)})
        prev = target

    return {"equity_curve": curve}
