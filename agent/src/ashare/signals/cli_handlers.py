"""CLI handlers for the signal delivery subsystem.

Usage:
    vibe-trading ashare signals list     --strategy <id> [--date YYYY-MM-DD]
    vibe-trading ashare signals test-push --provider <name> [--strategy <id>]
    vibe-trading ashare signals config   [--validate]

These commands are designed for **operator use**: they read the local
files written by the sinks (no API server required) so a user can
audit what's been delivered and quickly verify a webhook is wired up
correctly without waiting for a real strategy tick.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date as _date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_DEFAULT_ROOT = Path.home() / ".vibe-trading" / "ashare" / "signals"
_DEFAULT_AUDIT = Path.home() / ".vibe-trading" / "ashare" / "audit" / "signals.jsonl"


# --------------------------------------------------------------------------- #
# Path helpers                                                                #
# --------------------------------------------------------------------------- #


def _resolve_root(arg_root: str | None) -> Path:
    """Return the signals root from --root or the default."""
    return Path(arg_root).expanduser() if arg_root else _DEFAULT_ROOT


def _list_strategy_dirs(root: Path) -> list[str]:
    """List the strategy ids that have at least one file on disk."""
    if not root.is_dir():
        return []
    return sorted([p.name for p in root.iterdir() if p.is_dir()])


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


def cmd_signals_list(args: argparse.Namespace) -> int:
    """Show the day's signals for a strategy (or summarise across all)."""
    root = _resolve_root(args.root)
    if not root.is_dir():
        print(f"No signals directory at {root}; nothing to list.")
        return 0

    target_date = args.date or _date.today().isoformat()

    if args.strategy:
        # Single strategy: print the day's file in full.
        path = root / args.strategy / f"{target_date}.json"
        if not path.is_file():
            print(f"No file at {path}.")
            return 1
        data = json.loads(path.read_text(encoding="utf-8"))
        _print_strategy_day(args.strategy, data, json_output=args.json)
        return 0

    # No --strategy: summarize per-strategy counts for the day.
    print(f"Signals summary for {target_date} (root={root}):\n")
    any_found = False
    for sid in _list_strategy_dirs(root):
        path = root / sid / f"{target_date}.json"
        if not path.is_file():
            continue
        any_found = True
        data = json.loads(path.read_text(encoding="utf-8"))
        signals = data.get("signals", [])
        if args.json:
            print(json.dumps({"strategy_id": sid, **data}, ensure_ascii=False))
        else:
            counts: dict[str, int] = {}
            for s in signals:
                counts[s.get("side", "?")] = counts.get(s.get("side", "?"), 0) + 1
            side_str = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            print(f"  {sid:24}  {len(signals):>3} signals  [{side_str}]")
    if not any_found:
        print("  (no files for the requested date)")
    return 0


def _print_strategy_day(strategy_id: str, data: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps({"strategy_id": strategy_id, **data}, ensure_ascii=False, indent=2))
        return
    print(f"{strategy_id} — {data.get('trade_date', '?')}  ({data.get('run_type', '?')})")
    print(f"  generated_at: {data.get('generated_at', '?')}")
    print(f"  signals: {len(data.get('signals', []))}\n")
    for s in data.get("signals", []):
        meta = s.get("metadata", {}) or {}
        ref = s.get("ref_price") if s.get("ref_price") is not None else meta.get("ref_price")
        reason = s.get("reason", "")
        print(
            f"  {s.get('ts','?'):>19}  {s.get('symbol','?'):12}  "
            f"{s.get('side','?'):5}  ref={ref}  reason={reason}"
        )


# --------------------------------------------------------------------------- #
# test-push                                                                   #
# --------------------------------------------------------------------------- #


def cmd_signals_test_push(args: argparse.Namespace) -> int:
    """Send one synthetic signal to one provider to verify the webhook works.

    Always succeeds in the sense that "signal was accepted by the
    delivery service"; the actual HTTP outcome is logged by the sink
    and surfaces in the audit log under ``signal_emitted.failed_to``.
    Use ``vibe-trading ashare signals audit --tail 5`` to inspect.
    """
    asyncio.run(_async_test_push(args))
    return 0


async def _async_test_push(args: argparse.Namespace) -> None:
    from src.ashare.signals import get_delivery_service
    from src.ashare.signals.models import NormalizedSignal

    strategy_id = args.strategy or "test_push"
    symbol = args.symbol or "TEST.SH"
    side = args.side or "buy"
    ref_price = float(args.price) if args.price is not None else 0.0
    confidence = float(args.confidence) if args.confidence is not None else 0.99
    reason = f"test_push via CLI (provider={args.provider})"

    sig = NormalizedSignal(
        strategy_id=strategy_id,
        market_date=_date.today(),
        ts=__import__("datetime").datetime.now(),
        symbol=symbol, side=side,
        ref_price=ref_price,
        confidence=confidence,
        reason=reason,
        metadata={"test_push": True, "provider_hint": args.provider},
    )
    svc = get_delivery_service()
    print(f"Sending test signal: strategy={strategy_id} symbol={symbol} side={side} provider_hint={args.provider}")
    result = await svc.deliver(sig)
    print(f"  delivered_to: {result.delivered_to}")
    print(f"  failed_to:    {result.failed_to}")
    print(f"  deduped:      {result.deduped}")
    if args.provider and args.provider not in result.delivered_to and args.provider not in result.failed_to:
        print(
            f"  WARNING: provider '{args.provider}' was not invoked. Check that it is "
            f"enabled in ~/.vibe-trading/ashare/signals.yaml (sinks.webhook.providers).",
            file=sys.stderr,
        )
    if result.failed_to:
        print("  See the audit log for the per-sink failure reason.", file=sys.stderr)


# --------------------------------------------------------------------------- #
# audit                                                                       #
# --------------------------------------------------------------------------- #


def cmd_signals_audit(args: argparse.Namespace) -> int:
    """Tail the JSONL audit log."""
    path = Path(args.path).expanduser() if args.path else _DEFAULT_AUDIT
    if not path.is_file():
        print(f"No audit log at {path}.")
        return 1
    lines = path.read_text(encoding="utf-8").splitlines()
    tail = lines[-args.tail:] if args.tail > 0 else lines
    for line in tail:
        try:
            rec = json.loads(line)
        except ValueError:
            print(line)
            continue
        if args.json:
            print(json.dumps(rec, ensure_ascii=False))
        else:
            event = rec.get("event", "?")
            sid = rec.get("strategy_id", "?")
            sym = rec.get("symbol", "?")
            side = rec.get("side", "?")
            ref = rec.get("ref_price", "")
            extra = ""
            if event == "signal_emitted":
                d = rec.get("delivered_to", [])
                f = rec.get("failed_to", [])
                extra = f" → {d}" + (f" FAILED: {f}" if f else "")
            elif event == "signal_deduped":
                extra = f" reason={rec.get('reason', '?')}"
            print(f"  {rec.get('ts','?')}  {event:18}  {sid:18}  {sym:12} {side:5} ref={ref}{extra}")
    return 0


# --------------------------------------------------------------------------- #
# config                                                                      #
# --------------------------------------------------------------------------- #


def cmd_signals_config(args: argparse.Namespace) -> int:
    """Print (and optionally validate) the loaded signals config."""
    from src.ashare.signals.config import (
        DEFAULT_CONFIG_PATH, ConfigError, load_signals_config,
    )
    path = Path(args.path).expanduser() if args.path else DEFAULT_CONFIG_PATH
    if not path.is_file():
        print(f"No config at {path}.")
        if args.validate:
            return 1
        return 0
    print(f"Loading {path}...")
    try:
        cfg = load_signals_config(path=path)
    except ConfigError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(cfg.model_dump_json(indent=2))
        return 0
    print(f"  dedup.cooldown_seconds: {cfg.dedup.cooldown_seconds}")
    print(f"  sinks.local.enabled:     {cfg.sinks.local.enabled}")
    print(f"  sinks.sse.enabled:       {cfg.sinks.sse.enabled}")
    print(f"  sinks.webhook.enabled:   {cfg.sinks.webhook.enabled}")
    for p in cfg.sinks.webhook.providers:
        marker = "ON" if p.enabled else "off"
        f = p.filter
        fstr = f"min_conf={f.min_confidence} strategies={f.strategies} sides={f.sides}"
        print(f"    - {p.name:10} [{marker}]  {p.url_effective()[:60]}  filter: {fstr}")
    print(f"  audit.enabled:           {cfg.audit.enabled}")
    print(f"  audit.log_path:          {cfg.audit.log_path}")
    return 0


# --------------------------------------------------------------------------- #
# Subparser wiring                                                            #
# --------------------------------------------------------------------------- #


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``ashare signals`` subcommand family."""
    sig_parser = subparsers.add_parser(
        "signals", help="Signal delivery: list / test-push / audit / config"
    )
    sig_subs = sig_parser.add_subparsers(dest="signals_command")

    # list
    list_p = sig_subs.add_parser("list", help="List signals from local files")
    list_p.add_argument("--strategy", type=str, help="Filter by strategy id")
    list_p.add_argument("--date", type=str, help="Trade date (YYYY-MM-DD, default today)")
    list_p.add_argument("--root", type=str, help="Override signals root directory")
    list_p.add_argument("--json", action="store_true", help="Output JSON")
    list_p.set_defaults(_handler=cmd_signals_list)

    # test-push
    tp_p = sig_subs.add_parser("test-push", help="Send one synthetic signal to verify wiring")
    tp_p.add_argument("--provider", type=str, required=True, help="Provider name (informational only)")
    tp_p.add_argument("--strategy", type=str, help="Strategy id to attribute the signal to")
    tp_p.add_argument("--symbol", type=str, default="TEST.SH", help="Symbol (default TEST.SH)")
    tp_p.add_argument("--side", type=str, choices=["buy", "sell", "hold", "watch"], default="buy")
    tp_p.add_argument("--price", type=float, help="Reference price (default 0.0)")
    tp_p.add_argument("--confidence", type=float, help="Confidence 0-1 (default 0.99)")
    tp_p.set_defaults(_handler=cmd_signals_test_push)

    # audit
    au_p = sig_subs.add_parser("audit", help="Tail the JSONL audit log")
    au_p.add_argument("--tail", type=int, default=20, help="How many tail lines to show (default 20)")
    au_p.add_argument("--path", type=str, help="Override audit log path")
    au_p.add_argument("--json", action="store_true", help="Output JSON")
    au_p.set_defaults(_handler=cmd_signals_audit)

    # config
    cfg_p = sig_subs.add_parser("config", help="Show / validate signals.yaml")
    cfg_p.add_argument("--path", type=str, help="Override config path")
    cfg_p.add_argument("--validate", action="store_true", help="Exit non-zero if invalid")
    cfg_p.add_argument("--json", action="store_true", help="Output JSON")
    cfg_p.set_defaults(_handler=cmd_signals_config)


def dispatch(args: argparse.Namespace) -> int:
    """Dispatch ``ashare signals <subcommand>`` to the right handler."""
    handler = getattr(args, "_handler", None)
    if handler is None:
        # No subcommand given; print a hint.
        print("usage: vibe-trading ashare signals <list|test-push|audit|config> [...]", file=sys.stderr)
        return 2
    return handler(args)
