"""CLI handlers for A-share commands.

Usage:
    vibe-trading ashare limit-up [--date YYYY-MM-DD] [--sync]
    vibe-trading ashare portfolio [--create --name NAME --cash CASH]
    vibe-trading ashare report [--kind open|close|weekly] [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import requests


def _api_base() -> str:
    """Return the API base URL."""
    return "http://127.0.0.1:8899"


def _get(path: str) -> Any:
    """GET request to API."""
    res = requests.get(f"{_api_base()}{path}", timeout=30)
    if not res.ok:
        print(f"Error: {res.status_code} - {res.text}", file=sys.stderr)
        sys.exit(1)
    return res.json()


def _post(path: str, params: dict | None = None) -> Any:
    """POST request to API."""
    res = requests.post(f"{_api_base()}{path}", params=params, timeout=60)
    if not res.ok:
        print(f"Error: {res.status_code} - {res.text}", file=sys.stderr)
        sys.exit(1)
    return res.json()


def _today() -> str:
    return date.today().isoformat()


def cmd_limit_up(args: argparse.Namespace) -> int:
    """Show limit-up records for a date."""
    trade_date = args.date or _today()

    if args.sync:
        print(f"Syncing limit-up data for {trade_date}...")
        result = _post("/ashare/limit-up/sync", {"trade_date": trade_date})
        print(f"Synced {result['count']} records from {result['source']}")
        if result["errors"]:
            for err in result["errors"]:
                print(f"  Warning: {err}", file=sys.stderr)

    records = _get(f"/ashare/limit-up/{trade_date}")

    if args.json:
        print(json.dumps(records, indent=2, ensure_ascii=False))
        return 0

    if not records:
        print(f"No limit-up records for {trade_date}. Use --sync to fetch.")
        return 0

    # Print table
    print(f"\n涨停梯队 — {trade_date} ({len(records)} 条)\n")
    print(f"{'代码':<12} {'名称':<10} {'连板':>4} {'涨停价':>8} {'涨幅':>8} {'封单金额':>10} {'状态':>6}")
    print("-" * 70)
    for r in records:
        seal = f"{r['seal_amount'] / 1e4:.0f}万" if r["seal_amount"] else "—"
        status = "封板" if r["is_sealed"] else "炸板"
        print(
            f"{r['symbol']:<12} {r['name']:<10} {r['limit_up_count']:>4} "
            f"{r['limit_up_price']:>8.2f} {r['change_pct']*100:>7.2f}% "
            f"{seal:>10} {status:>6}"
        )
    print()
    return 0


def cmd_portfolio(args: argparse.Namespace) -> int:
    """Manage paper portfolios."""
    if args.create:
        res = requests.post(
            f"{_api_base()}/ashare/portfolios",
            json={"name": args.name or "A股模拟账户", "initial_cash": args.cash or 300_000.0},
            timeout=30,
        )
        if not res.ok:
            print(f"Error: {res.status_code} - {res.text}", file=sys.stderr)
            return 1
        p = res.json()
        print(f"Created portfolio: {p['portfolio_id']}")
        print(f"  Name: {p['name']}")
        print(f"  Initial cash: ¥{p['initial_cash']:,.2f}")
        return 0

    portfolios = _get("/ashare/portfolios")
    if args.json:
        print(json.dumps(portfolios, indent=2, ensure_ascii=False))
        return 0

    if not portfolios:
        print("No portfolios. Use --create to create one.")
        return 0

    print(f"\n模拟持仓账户 ({len(portfolios)} 个)\n")
    print(f"{'ID':<12} {'名称':<12} {'总资产':>12} {'现金':>12} {'盈亏':>12} {'收益率':>8}")
    print("-" * 70)
    for p in portfolios:
        pnl_color = "\033[32m" if p["total_pnl"] >= 0 else "\033[31m"
        reset = "\033[0m"
        print(
            f"{p['portfolio_id']:<12} {p['name']:<12} "
            f"¥{p['total_value']:>10,.2f} ¥{p['cash']:>10,.2f} "
            f"{pnl_color}¥{p['total_pnl']:>10,.2f}{reset} {p['total_return_pct']:>7.2f}%"
        )
    print()
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Generate or show market report."""
    kind = args.kind or "close"
    trade_date = args.date or _today()

    if args.generate:
        print(f"Generating {kind} report for {trade_date}...")
        result = _post(f"/ashare/reports/{kind}", {"trade_date": trade_date})
        print(f"Report generated: {result['title']}")

    report = _get(f"/ashare/reports/{kind}/{trade_date}")
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    print(f"\n{report.get('kind', kind).upper()} 报告 — {trade_date}\n")
    print(report.get("markdown", "No report found."))
    return 0


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the 'ashare' subcommand."""
    ashare_parser = subparsers.add_parser("ashare", help="A-share market tools (limit-up, portfolio, reports, signals)")
    ashare_subparsers = ashare_parser.add_subparsers(dest="ashare_command")

    # limit-up
    lu_parser = ashare_subparsers.add_parser("limit-up", help="Show limit-up records")
    lu_parser.add_argument("--date", type=str, help="Trade date (YYYY-MM-DD)")
    lu_parser.add_argument("--sync", action="store_true", help="Sync data from data source first")
    lu_parser.add_argument("--json", action="store_true", help="Output JSON")

    # portfolio
    pf_parser = ashare_subparsers.add_parser("portfolio", help="Manage paper portfolios")
    pf_parser.add_argument("--create", action="store_true", help="Create a new portfolio")
    pf_parser.add_argument("--name", type=str, help="Portfolio name")
    pf_parser.add_argument("--cash", type=float, help="Initial cash (default 300,000)")
    pf_parser.add_argument("--json", action="store_true", help="Output JSON")

    # report
    rp_parser = ashare_subparsers.add_parser("report", help="Market reports")
    rp_parser.add_argument("--kind", choices=["open", "close", "weekly"], help="Report kind")
    rp_parser.add_argument("--date", type=str, help="Trade date (YYYY-MM-DD)")
    rp_parser.add_argument("--generate", action="store_true", help="Generate report")
    rp_parser.add_argument("--json", action="store_true", help="Output JSON")


    # signals (signal delivery: list / test-push / audit / config)
    from src.ashare.signals.cli_handlers import add_subparser as _add_signals_subparser
    _add_signals_subparser(ashare_subparsers)


def _coerce_exit_code(rc: Any) -> int:
    """Coerce a handler return value into an integer exit code.

    CLI handlers historically return ``int`` directly, but a few helper
    functions may return ``None`` (e.g. argparse-driven error paths).
    This helper normalises both into an int so the top-level
    :func:`dispatch` never crashes on a stray ``None``.
    """
    return int(rc) if rc is not None else 0


def dispatch(args: argparse.Namespace) -> int:
    """Dispatch ashare subcommands."""
    if args.ashare_command == "limit-up":
        return cmd_limit_up(args)
    if args.ashare_command == "portfolio":
        return cmd_portfolio(args)
    if args.ashare_command == "report":
        return cmd_report(args)
    if args.ashare_command == "signals":
        from src.ashare.signals.cli_handlers import dispatch as _signals_dispatch
        return _coerce_exit_code(_signals_dispatch(args))
    print("Error: ashare requires a subcommand. Try: vibe-trading ashare limit-up", file=sys.stderr)
    return 1
