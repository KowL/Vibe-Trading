"""CLI handlers for ``vibe-trading strategy mine ...``.

Prints a JSON envelope to stdout and writes a strategy config plus a detailed
report to ``~/.vibe-trading/strategies/`` by default.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from src.strategy_mining.miner import RollingICMiner, StrategyConfig
from src.strategy_mining.race import StrategyRace
from src.strategy_mining.search import WalkForwardGridSearch

try:
    from src.hypotheses.registry import HypothesisRegistry

    _HAS_HYPOTHESIS = True
except Exception:  # pragma: no cover
    _HAS_HYPOTHESIS = False

try:
    from rich.console import Console

    _console: Console | None = Console()
except Exception:  # pragma: no cover
    _console = None


_UNIVERSE_CHOICES = ["csi300", "sp500", "btc-usdt"]
_DEFAULT_OUTPUT_DIR = Path.home() / ".vibe-trading" / "strategies"


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _handle_exception(args: argparse.Namespace, prefix: str, exc: BaseException) -> int:
    _err(f"{prefix}: {exc}")
    if getattr(args, "verbose", False):
        traceback.print_exception(type(exc), exc, exc.__traceback__)
    if "TUSHARE_TOKEN" in str(exc):
        _err("")
        _err("How to fix:")
        _err("  1. Register for a free token at https://tushare.pro/register")
        _err("  2. Add 'TUSHARE_TOKEN=<your_token>' to agent/.env  (or ~/.vibe-trading/.env)")
        _err("  3. Re-run this command")
    return 1


def _infer_status(metrics: dict[str, float]) -> str:
    """Map backtest metrics to a hypothesis lifecycle status."""
    sharpe = metrics.get("sharpe", 0.0)
    max_dd = metrics.get("max_drawdown_pct", 100.0)
    if sharpe >= 1.0 and max_dd < 25.0:
        return "validated"
    if sharpe >= 0.5 and max_dd < 35.0:
        return "testing"
    return "exploring"


def _record_hypothesis(
    *,
    universe: str,
    period: str,
    metrics: dict[str, float],
    config_path: Path,
    report_path: Path,
    search_path: Path | None,
    selected_alphas: list[str],
    params: dict[str, Any] | None = None,
) -> str | None:
    """Create a hypothesis and link the mined strategy artifacts."""
    if not _HAS_HYPOTHESIS:
        return None
    try:
        reg = HypothesisRegistry()
        status = _infer_status(metrics)
        title = f"Rolling-IC strategy for {universe} ({period})"
        thesis = (
            "Multi-factor strategy mined from the Alpha Zoo using rolling-IC "
            "alpha selection, theme balancing, and weekly equal-weight rebalancing."
        )
        if params:
            thesis += f" Optimised via walk-forward CV with params: {params}."
        hyp = reg.create(
            title=title,
            thesis=thesis,
            status=status,
            universe=universe,
            signal_definition=f"Top-{metrics.get('top_n', 'N')} equal-weight from selected alphas: {selected_alphas}",
            data_sources=["alpha_zoo", universe],
            skills=["alpha-zoo", "strategy_mining"],
        )
        notes = f"config={config_path.name}, report={report_path.name}"
        if search_path:
            notes += f", search={search_path.name}"
        reg.link_backtest(
            hyp.hypothesis_id,
            run_card_path=str(config_path),
            metrics=metrics,
            notes=notes,
        )
        return hyp.hypothesis_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not record hypothesis: %s", exc)
        return None


def cmd_strategy_mine(args: argparse.Namespace) -> int:
    """Run the rolling-IC strategy miner and persist results."""
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        miner = RollingICMiner(
            universe=args.universe,
            period=args.period,
            train_years=args.train_years,
            rebalance_freq="weekly",
            top_n=args.top_n,
            max_per_theme=args.max_per_theme,
            min_ic=args.min_ic,
            min_ic_positive_ratio=args.min_ic_positive_ratio,
            min_t_stat=args.min_t_stat,
            commission=args.commission,
            slippage=args.slippage,
            use_market_filter=not args.no_market_filter,
            use_random_control=args.strict,
            n_random_seeds=args.n_random_seeds,
            alpha_t_threshold=args.alpha_t_threshold,
            neutralize=args.neutralize,
            neutralize_fields=[f.strip() for f in args.neutralize_fields.split(",") if f.strip()],
            market_cap_buckets=args.market_cap_buckets,
            replacement_buffer=args.replacement_buffer,
        )
        result = miner.mine()

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        config_path = output_dir / f"strategy_{ts}.json"
        report_path = output_dir / f"strategy_report_{ts}.json"

        result.config.write(config_path)
        report_path.write_text(
            json.dumps(result.report, indent=2, default=str),
            encoding="utf-8",
        )

        hypothesis_id = _record_hypothesis(
            universe=args.universe,
            period=args.period,
            metrics=result.metrics,
            config_path=config_path,
            report_path=report_path,
            search_path=None,
            selected_alphas=result.config.selected_alphas,
        )

        envelope = {
            "status": "ok",
            "config_path": str(config_path),
            "report_path": str(report_path),
            "hypothesis_id": hypothesis_id,
            "metrics": result.metrics,
            "n_rebalances": result.report.get("n_rebalances"),
            "selected_alphas": result.config.selected_alphas,
        }

        print(json.dumps(envelope, indent=2, default=str))

        if _console is not None:
            _console.print(
                f"[green]✓ Mined strategy saved:[/green] {config_path}\n"
                f"[dim]Report:[/dim] {report_path}"
            )
        else:
            print(f"Mined strategy saved: {config_path}", file=sys.stderr)
            print(f"Report: {report_path}", file=sys.stderr)
        return 0
    except Exception as exc:  # noqa: BLE001
        return _handle_exception(args, "strategy mine failed", exc)


def cmd_strategy_search(args: argparse.Namespace) -> int:
    """Run walk-forward grid search and persist the best strategy."""
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    param_grid: dict[str, list[Any]] | None = None
    if args.param_grid:
        try:
            param_grid = json.loads(args.param_grid)
            if not isinstance(param_grid, dict):
                raise ValueError("--param-grid must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            return _handle_exception(args, "invalid --param-grid", exc)

    # Ensure the CLI-level replacement-buffer is part of every candidate unless
    # the user already included it in the grid.
    if param_grid is None:
        param_grid = {}
    if "replacement_buffer" not in param_grid:
        param_grid["replacement_buffer"] = [args.replacement_buffer]

    try:
        search = WalkForwardGridSearch(
            universe=args.universe,
            period=args.period,
            param_grid=param_grid,
            n_folds=args.n_folds,
            metric=args.metric,
            neutralize=args.neutralize,
            neutralize_fields=[f.strip() for f in args.neutralize_fields.split(",") if f.strip()],
            market_cap_buckets=args.market_cap_buckets,
        )
        result = search.fit()

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        config_path = output_dir / f"strategy_{ts}.json"
        report_path = output_dir / f"strategy_report_{ts}.json"
        search_path = output_dir / f"strategy_search_{ts}.json"

        result.final_result.config.write(config_path)
        report_path.write_text(
            json.dumps(result.final_result.report, indent=2, default=str),
            encoding="utf-8",
        )
        result.write(search_path)

        hypothesis_id = _record_hypothesis(
            universe=args.universe,
            period=args.period,
            metrics=result.final_result.metrics,
            config_path=config_path,
            report_path=report_path,
            search_path=search_path,
            selected_alphas=result.final_result.config.selected_alphas,
            params=result.best_params,
        )

        envelope = {
            "status": "ok",
            "best_params": result.best_params,
            "best_score": result.best_score,
            "metric": result.metric,
            "n_folds": result.n_folds,
            "config_path": str(config_path),
            "report_path": str(report_path),
            "search_path": str(search_path),
            "hypothesis_id": hypothesis_id,
            "metrics": result.final_result.metrics,
            "selected_alphas": result.final_result.config.selected_alphas,
        }

        print(json.dumps(envelope, indent=2, default=str))

        if _console is not None:
            _console.print(
                f"[green]✓ Best strategy saved:[/green] {config_path}\n"
                f"[dim]Search summary:[/dim] {search_path}"
            )
        else:
            print(f"Best strategy saved: {config_path}", file=sys.stderr)
            print(f"Search summary: {search_path}", file=sys.stderr)
        return 0
    except Exception as exc:  # noqa: BLE001
        return _handle_exception(args, "strategy search failed", exc)


def cmd_strategy_race(args: argparse.Namespace) -> int:
    """Run a horse race between multiple strategy candidates."""
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[dict[str, Any]] | None = None
    if args.candidates:
        try:
            with open(args.candidates, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if not isinstance(loaded, list):
                raise ValueError("--candidates file must contain a JSON list")
            candidates = loaded
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return _handle_exception(args, "invalid --candidates file", exc)

    try:
        race = StrategyRace(
            universe=args.universe,
            period=args.period,
            candidates=candidates,
            race_window=args.race_window,
            metric=args.metric,
        )
        result = race.run()

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        config_path = output_dir / f"strategy_{ts}.json"
        report_path = output_dir / f"strategy_report_{ts}.json"
        race_path = output_dir / f"strategy_race_{ts}.json"

        result.final_result.config.write(config_path)
        report_path.write_text(
            json.dumps(result.final_result.report, indent=2, default=str),
            encoding="utf-8",
        )
        result.write(race_path)

        hypothesis_id = _record_hypothesis(
            universe=args.universe,
            period=args.period,
            metrics=result.final_result.metrics,
            config_path=config_path,
            report_path=report_path,
            search_path=race_path,
            selected_alphas=result.final_result.config.selected_alphas,
            params={"winner": result.best_name, **result.best_params},
        )

        envelope = {
            "status": "ok",
            "winner": result.best_name,
            "best_params": result.best_params,
            "best_score": result.best_score,
            "metric": result.metric,
            "race_window": result.race_window,
            "config_path": str(config_path),
            "report_path": str(report_path),
            "race_path": str(race_path),
            "hypothesis_id": hypothesis_id,
            "metrics": result.final_result.metrics,
            "selected_alphas": result.final_result.config.selected_alphas,
        }

        print(json.dumps(envelope, indent=2, default=str))

        if _console is not None:
            _console.print(
                f"[green]✓ Winning strategy saved:[/green] {config_path}\n"
                f"[dim]Race summary:[/dim] {race_path}"
            )
        else:
            print(f"Winning strategy saved: {config_path}", file=sys.stderr)
            print(f"Race summary: {race_path}", file=sys.stderr)
        return 0
    except Exception as exc:  # noqa: BLE001
        return _handle_exception(args, "strategy race failed", exc)


_DISPATCH: dict[str, Any] = {
    "mine": cmd_strategy_mine,
    "search": cmd_strategy_search,
    "race": cmd_strategy_race,
}


_STRATEGY_PARSER: argparse.ArgumentParser | None = None


def add_subparser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``strategy`` and its subcommands."""
    global _STRATEGY_PARSER

    strategy_parser = subparsers.add_parser(
        "strategy", help="Strategy mining: discover multi-factor strategies"
    )
    strategy_parser.add_argument(
        "--verbose", action="store_true", help="Show full traceback on errors"
    )
    strategy_sub = strategy_parser.add_subparsers(dest="strategy_command")

    p_mine = strategy_sub.add_parser("mine", help="Mine a rolling-IC strategy from the Alpha Zoo")
    p_mine.add_argument(
        "--universe",
        default="csi300",
        choices=_UNIVERSE_CHOICES,
        help=f"Universe (default: csi300; one of {', '.join(_UNIVERSE_CHOICES)})",
    )
    p_mine.add_argument(
        "--period",
        default="2020-2025",
        help="Period spec: YYYY-YYYY or YYYY-MM-DD/YYYY-MM-DD (e.g. 2020-2025)",
    )
    p_mine.add_argument(
        "--train-years",
        type=int,
        default=3,
        help="Rolling training window in years (default: 3)",
    )
    p_mine.add_argument(
        "--top-n",
        type=int,
        default=30,
        help="Number of stocks in the equal-weight portfolio (default: 30)",
    )
    p_mine.add_argument(
        "--max-per-theme",
        type=int,
        default=3,
        help="Max alphas per theme (default: 3)",
    )
    p_mine.add_argument(
        "--min-ic",
        type=float,
        default=0.02,
        help="Minimum mean IC for an alpha to be selected (default: 0.02)",
    )
    p_mine.add_argument(
        "--min-ic-positive-ratio",
        type=float,
        default=0.55,
        help="Minimum IC-positive ratio (default: 0.55)",
    )
    p_mine.add_argument(
        "--min-t-stat",
        type=float,
        default=2.0,
        help="Minimum |t-stat| for an alpha to be selected (default: 2.0)",
    )
    p_mine.add_argument(
        "--commission",
        type=float,
        default=0.0003,
        help="One-side commission fraction (default: 0.0003)",
    )
    p_mine.add_argument(
        "--slippage",
        type=float,
        default=0.001,
        help="One-side slippage fraction (default: 0.001)",
    )
    p_mine.add_argument(
        "--strict",
        action="store_true",
        help="Use same-universe random-control confirmed_alive gate (slower, more robust)",
    )
    p_mine.add_argument(
        "--n-random-seeds",
        type=int,
        default=5,
        help="Number of random shuffles when --strict is used (default: 5)",
    )
    p_mine.add_argument(
        "--alpha-t-threshold",
        type=float,
        default=2.0,
        help="t-stat threshold for the strict random-control gate (default: 2.0)",
    )
    p_mine.add_argument(
        "--neutralize",
        action="store_true",
        help="Neutralise composite scores by sector / market-cap before selecting stocks",
    )
    p_mine.add_argument(
        "--neutralize-fields",
        default="sector",
        help="Comma-separated panel fields to neutralise on (default: sector)",
    )
    p_mine.add_argument(
        "--market-cap-buckets",
        type=int,
        default=5,
        help="Number of market-cap buckets when neutralising on market_cap (default: 5)",
    )
    p_mine.add_argument(
        "--replacement-buffer",
        type=float,
        default=0.0,
        help="Sticky Top-N buffer; previous holdings within this score margin of the new cutoff are kept (default: 0.0)",
    )
    p_mine.add_argument(
        "--no-market-filter",
        action="store_true",
        help="Disable the MA20>MA60 market trend filter",
    )
    p_mine.add_argument(
        "--output-dir",
        default=str(_DEFAULT_OUTPUT_DIR),
        help="Directory to write config and report JSON (default: ~/.vibe-trading/strategies)",
    )

    p_search = strategy_sub.add_parser(
        "search", help="Walk-forward grid search over strategy hyperparameters"
    )
    p_search.add_argument(
        "--universe",
        default="csi300",
        choices=_UNIVERSE_CHOICES,
        help=f"Universe (default: csi300; one of {', '.join(_UNIVERSE_CHOICES)})",
    )
    p_search.add_argument(
        "--period",
        default="2020-2025",
        help="Period spec: YYYY-YYYY or YYYY-MM-DD/YYYY-MM-DD (e.g. 2020-2025)",
    )
    p_search.add_argument(
        "--n-folds",
        type=int,
        default=3,
        help="Number of walk-forward validation folds (default: 3)",
    )
    p_search.add_argument(
        "--metric",
        default="sharpe",
        choices=["sharpe", "information_ratio", "annual_return_pct", "calmar"],
        help="Validation metric (default: sharpe)",
    )
    p_search.add_argument(
        "--param-grid",
        default=None,
        help='JSON dict of parameter lists, e.g. \'{"top_n": [20, 30]}\'',
    )
    p_search.add_argument(
        "--strict",
        action="store_true",
        help="Use same-universe random-control confirmed_alive gate (slower, more robust)",
    )
    p_search.add_argument(
        "--n-random-seeds",
        type=int,
        default=5,
        help="Number of random shuffles when --strict is used (default: 5)",
    )
    p_search.add_argument(
        "--alpha-t-threshold",
        type=float,
        default=2.0,
        help="t-stat threshold for the strict random-control gate (default: 2.0)",
    )
    p_search.add_argument(
        "--neutralize",
        action="store_true",
        help="Neutralise composite scores by sector / market-cap",
    )
    p_search.add_argument(
        "--neutralize-fields",
        default="sector",
        help="Comma-separated panel fields to neutralise on (default: sector)",
    )
    p_search.add_argument(
        "--market-cap-buckets",
        type=int,
        default=5,
        help="Number of market-cap buckets when neutralising on market_cap (default: 5)",
    )
    p_search.add_argument(
        "--replacement-buffer",
        type=float,
        default=0.0,
        help="Sticky Top-N buffer applied to every candidate (default: 0.0)",
    )
    p_search.add_argument(
        "--output-dir",
        default=str(_DEFAULT_OUTPUT_DIR),
        help="Directory to write artifacts (default: ~/.vibe-trading/strategies)",
    )

    p_race = strategy_sub.add_parser(
        "race", help="Horse race multiple strategy candidates and pick the winner"
    )
    p_race.add_argument(
        "--universe",
        default="csi300",
        choices=_UNIVERSE_CHOICES,
        help=f"Universe (default: csi300; one of {', '.join(_UNIVERSE_CHOICES)})",
    )
    p_race.add_argument(
        "--period",
        default="2020-2025",
        help="Period spec: YYYY-YYYY or YYYY-MM-DD/YYYY-MM-DD (e.g. 2020-2025)",
    )
    p_race.add_argument(
        "--candidates",
        default=None,
        help="Path to JSON file with a list of candidate parameter dicts (each with a 'name' key)",
    )
    p_race.add_argument(
        "--race-window",
        default="6M",
        help="Recent window to evaluate candidates on, e.g. 6M or 90D (default: 6M)",
    )
    p_race.add_argument(
        "--metric",
        default="sharpe",
        choices=["sharpe", "information_ratio", "annual_return_pct", "calmar"],
        help="Ranking metric (default: sharpe)",
    )
    p_race.add_argument(
        "--output-dir",
        default=str(_DEFAULT_OUTPUT_DIR),
        help="Directory to write artifacts (default: ~/.vibe-trading/strategies)",
    )

    _STRATEGY_PARSER = strategy_parser
    return strategy_parser


def dispatch(args: argparse.Namespace) -> int:
    """Dispatch ``strategy <sub>`` to the matching handler."""
    sub = getattr(args, "strategy_command", None)
    if sub is None:
        if _STRATEGY_PARSER is not None:
            _STRATEGY_PARSER.print_help()
        else:
            _err("strategy requires a subcommand. Try: vibe-trading strategy mine")
        return 1
    handler = _DISPATCH.get(sub)
    if handler is None:
        _err(f"strategy: unknown subcommand {sub!r}")
        return 1
    return handler(args)
