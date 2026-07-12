"""Manual end-to-end script for /ashare/strategy/compare.

Usage:
    venv/bin/python -m scripts.run_strategy_compare_e2e \
      --universe csi300 \
      --start 2025-01-01 --end 2025-03-01 \
      --strategies-json '[{"name":"local-baseline","selector":"local_select","params":{"top_n":20,"rebalance_days":5}}]'

Requires:
    - The API server is running and reachable at ``--host``.
    - Real adshare parquet is available to the server.
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import urljoin

import requests


def main() -> int:
    parser = argparse.ArgumentParser(description="E2E smoke test for strategy compare")
    parser.add_argument("--host", default="http://127.0.0.1:8000")
    parser.add_argument("--universe", default="csi300")
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2025-03-01")
    parser.add_argument("--initial-cash", type=float, default=1_000_000)
    parser.add_argument("--commission-bps", type=float, default=3)
    parser.add_argument("--slippage-bps", type=float, default=5)
    parser.add_argument(
        "--strategies-json",
        default=json.dumps(
            [
                {
                    "name": "local-baseline",
                    "selector": "local_select",
                    "params": {"top_n": 20, "rebalance_days": 5},
                },
                {
                    "name": "mf-baseline",
                    "selector": "multi_factor",
                    "params": {"top_n": 20, "rebalance_days": 5},
                },
            ]
        ),
    )
    args = parser.parse_args()

    strategies = json.loads(args.strategies_json)
    if len(strategies) < 2:
        print("Need at least 2 strategies for compare", file=sys.stderr)
        return 1

    payload = {
        "shared": {
            "start_date": args.start,
            "end_date": args.end,
            "initial_cash": args.initial_cash,
            "universe": args.universe,
            "commission_bps": args.commission_bps,
            "slippage_bps": args.slippage_bps,
        },
        "strategies": strategies,
    }

    url = urljoin(args.host, "/ashare/strategy/compare")
    print(f"POST {url}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    resp = requests.post(url, json=payload, timeout=300)
    print(f"\nHTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        print(resp.text)
        return 1

    print(json.dumps(data, indent=2, ensure_ascii=False))

    if resp.status_code != 200:
        print("\nFAILED: non-200 status", file=sys.stderr)
        return 1

    metrics = data.get("metrics", [])
    curves = data.get("curves", [])
    alignment = data.get("alignment", {})

    if len(metrics) != len(strategies):
        print("FAILED: metrics length mismatch", file=sys.stderr)
        return 1

    if len(curves) != len(strategies):
        print("FAILED: curves length mismatch", file=sys.stderr)
        return 1

    if curves and len(curves[0].get("points", [])) != len(curves[-1].get("points", [])):
        print("FAILED: curves are not aligned to the same dates", file=sys.stderr)
        return 1

    common_dates = alignment.get("common_dates", [])
    if len(common_dates) < 30:
        print(f"FAILED: only {len(common_dates)} common dates", file=sys.stderr)
        return 1

    print("\n--- Summary ---")
    print(f"Coverage ratio: {alignment.get('coverage_ratio', 0):.2%}")
    print(f"Common dates: {len(common_dates)}")
    for m in metrics:
        print(
            f"  {m['name']} ({m['selector']}): trades={m['num_trades']}, "
            f"sharpe={m['sharpe']}, return={m['total_return_pct']:.2f}%"
        )

    ranking = sorted(metrics, key=lambda x: x["sharpe"], reverse=True)
    print("\nSharpe ranking:")
    for i, m in enumerate(ranking, 1):
        print(f"  {i}. {m['name']} ({m['selector']}): sharpe={m['sharpe']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
