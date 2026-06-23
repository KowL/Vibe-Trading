# Strategy Compare — Design Spec

- Date: 2026-06-23
- Owner: Vibe-Trading agent
- Status: Draft (post-brainstorming, pre-writing-plans)
- Scope: A-share strategy subsystem only

## § 0. In-flight context (decoupling note)

This spec is written against a working tree that already contains uncommitted changes on branch `dev/ruo-ai-integration`:

- `frontend/src/pages/ashare/StrategyPage.tsx` — large in-flight rewrite (ECharts equity curve, tab-scoped state, metric tiles, trades table, section header). A live Vite/HMR warning (`Multiple exports with the same name "default"`) is reported at line 367; the root cause is a duplicate `export default StrategyPage` emission by the Fast Refresh transform on the rewritten file.
- `agent/src/ashare/api/routes.py` — `strategy_select` switched to `local_select`; `strategy_backtest` now exposes `equity_curve` and `trades` in the response.
- `agent/api_server.py` — `getattr(route, "path", None)` guard added before SPA mount check.
- `docker-compose.yml` — host `adshare` parquet directory mounted as `/app/adshare/data:ro`.

**This spec does not touch any of the four files above** except `StrategyPage.tsx`, where we add a single `<Link to="/ashare/strategy/compare">` button (~10 lines + 2 imports) **without modifying the in-flight Tab state, types, or HMR-unsafe export shape**. The HMR duplicate-default warning is **out of scope** and is left as a separate fix-up PR. The new `run_backtest(spec, shared)` internal function (see § 3) is called from both the new compare route and the existing `strategy_backtest` route, so the two code paths share identical backtest semantics; the only contract change on `strategy_backtest` is that its implementation is now a thin wrapper that delegates to `run_backtest` and projects the result into the existing `BacktestResult` schema.

If the in-flight changes above are amended in a separate PR, the only contract this spec depends on is:

1. `agent.src.ashare.strategies.local_select.local_select(trade_date, top_n) -> list[StockPick]` exists and is importable.
2. `agent.src.ashare.strategies.multi_factor.MultiFactorSelector().select(trade_date, top_n) -> list[StockPick]` exists and is importable.
3. `agent.src.ashare.strategies.local_loader.load_panel(universe, start_date, end_date)` returns a DataFrame-like object with a date index and price columns, and is safe to call from multiple threads.

If any of those signatures change, this spec must be re-read before implementation.

## § 1. Goals & non-goals

**Goals**

1. New `POST /ashare/strategy/compare` endpoint. Accepts 2–4 "selector + selector hyperparameters" specs, with a shared `(start_date, end_date, initial_cash, universe, commission_bps, slippage_bps)` envelope, runs all backtests in parallel, aligns by trading-day intersection, and returns a metric table + a single multi-line equity curve.
2. Extract `run_backtest(spec, shared) -> BacktestOutcome` as an internal function. The existing `strategy_backtest` route becomes a thin wrapper that delegates to it and projects the outcome into the current `BacktestResult` schema. Both the `/backtest` and `/compare` routes share one backtest implementation, which makes "fair compare" structurally impossible to break.
3. New `selector_registry` decorator + registry. Currently registers `local_select` and `multi_factor`. New selectors are added by writing a function and decorating it.
4. New `ComparePage` at `/ashare/strategy/compare`: 2–4 inline strategy cards + a shared-params form + a results area (metric comparison table + ECharts multi-line equity curve). `StrategyPage` gains a single "Open compare" button that links to this route.

**Non-goals**

1. **Difference attribution / overlap analysis** (i.e. B1c-i-2 / B1c-i-3). Defer to a follow-up spec.
2. **Persisted compare runs**. No `agent/runs` landing, no SSE/polling. Synchronous request–response.
3. **More selectors**. Only `local_select` and `multi_factor` are accepted. `trend_timing`, `wanrun_band`, `adaptive_backtest` are out.
4. **Raising the N cap** beyond 4. Hard-capped, otherwise 422.
5. **Cross-universe / cross-fee compare**. Shared params are required; specs cannot override them.
6. **Touching the in-flight `StrategyPage.tsx` rewrite** beyond adding the link button. We do not fix the HMR duplicate-default warning here.
7. **No broker / live-trading integration**. This is a research / backtesting surface.
8. **No changes to `api_server.py` or `docker-compose.yml`** unless the compare route strictly needs a new env var (it does not).

## § 2. API surface & data model

Implementation files: `agent/src/ashare/strategies/compare_models.py` (new) and `agent/src/ashare/api/routes.py` (add `POST /ashare/strategy/compare`).

### 2.1 Shared params — `StrategyCompareShared`

```python
class StrategyCompareShared(BaseModel):
    start_date: date
    end_date: date
    initial_cash: float = Field(ge=10_000, le=100_000_000)
    universe: Literal["csi300", "csi500", "csi1000", "all_a"]
    commission_bps: float = Field(ge=0, le=50)
    slippage_bps: float = Field(ge=0, le=50)
```

Validation: `end_date > start_date`, total span ≤ 5 years. `top_n`, `rebalance_days`, factor weights, and risk guards do **not** live here — they live in the per-spec `params` (different strategies may choose different hyperparameters; "fair compare" applies only to market friction).

### 2.2 Strategy spec — `StrategySpec`

```python
class StrategySpec(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    selector: Literal["local_select", "multi_factor"]
    params: SelectorParams

class LocalSelectParams(BaseModel):
    top_n: int = Field(ge=5, le=100, default=20)
    rebalance_days: int = Field(ge=1, le=60, default=5)

class MultiFactorParams(BaseModel):
    top_n: int = Field(ge=5, le=100, default=20)
    rebalance_days: int = Field(ge=1, le=60, default=5)
    factor_weights: dict[str, float] | None = None

SelectorParams = Annotated[
    LocalSelectParams | MultiFactorParams,
    Field(discriminator="__selector_kind__"),
]
```

`factor_weights` is a hook for "future factor-zoo expansion" but this PR does not implement weight merging: if provided, it is passed through to `MultiFactorSelector`; if `None`, the selector's default weights are used.

`name` is unique-validated at the request level (see § 2.3) and is what the response uses to key metrics, curves, and dropped-date counts.

### 2.3 Request — `StrategyCompareRequest`

```python
class StrategyCompareRequest(BaseModel):
    shared: StrategyCompareShared
    strategies: list[StrategySpec] = Field(min_length=2, max_length=4)
```

422 validation (in addition to Pydantic defaults):

- `strategies` length ∈ [2, 4]
- `strategies[i].name` unique across the list
- `start_date < end_date`, total span ≤ 5 years
- `params` type matches `selector` (discriminated union)

### 2.4 Response — `StrategyCompareResponse`

```python
class StrategyMetrics(BaseModel):
    name: str
    selector: str
    start_date: date
    end_date: date
    initial_cash: float
    final_value: float
    total_return_pct: float
    annualized_return_pct: float | None         # null when span < 30 days
    max_drawdown_pct: float
    sharpe: float
    profit_factor: float
    num_trades: int
    avg_holding_days: float

class CurvePoint(BaseModel):
    date: date
    total_value: float
    drawdown_pct: float
    num_positions: int

class AlignedCurve(BaseModel):
    name: str
    points: list[CurvePoint]

class AlignmentInfo(BaseModel):
    common_dates: list[date]
    per_strategy_dropped: dict[str, int]
    coverage_ratio: float
    warning: Literal["low_coverage"] | None = None   # set when coverage_ratio < 0.7

class StrategyCompareResponse(BaseModel):
    shared: StrategyCompareShared
    alignment: AlignmentInfo
    metrics: list[StrategyMetrics]    # length N, same order as request strategies
    curves: list[AlignedCurve]        # length N
```

The existing `BacktestResult` schema on `/backtest` is unchanged. `StrategyMetrics` is a flat subset of it; `BacktestOutcome` (the internal type returned by `run_backtest`) carries the full `equity_curve` and `trades`, and the route projects that down to `StrategyMetrics` for the compare response.

## § 3. Compare engine internals

Implementation files:

- `agent/src/ashare/strategies/selector_registry.py` (new)
- `agent/src/ashare/strategies/compare_backtest.py` (new — `run_backtest` + `BacktestOutcome`)
- `agent/src/ashare/strategies/compare_engine.py` (new — `run_compare`, alignment, dispatch)
- `agent/src/ashare/strategies/local_select.py` (add `@register_selector("local_select")` wrapper)
- `agent/src/ashare/strategies/multi_factor.py` (add `@register_selector("multi_factor")` wrapper)
- `agent/src/ashare/strategies/backtest.py` (extract `_compute_metrics` as a private helper)
- `agent/src/ashare/api/routes.py` (`strategy_backtest` becomes a thin wrapper; add `strategy_compare`)

### 3.1 `selector_registry` — decorator + registry

```python
class SelectorFn(Protocol):
    def __call__(self, *, trade_date: date, top_n: int, params: dict) -> list[StockPick]: ...

_REGISTRY: dict[str, SelectorFn] = {}

def register_selector(name: str) -> Callable[[SelectorFn], SelectorFn]: ...
def resolve_selector(name: str) -> SelectorFn: ...  # raises UnknownSelectorError
def list_selectors() -> list[str]: ...

class UnknownSelectorError(KeyError): ...
```

Wiring: `routes.py` imports the registry module for its side effects, which in turn imports the two selector wrappers, which register themselves on import. New selectors are added by:

1. Writing the selector function in its own module.
2. Adding `@register_selector("name")` + an `import` line in `selector_registry.py`.

The registry signature takes `params: dict` (not the typed Pydantic model) so adding a new selector does not change the registry interface.

### 3.2 `run_backtest` extraction

```python
@dataclass
class BacktestOutcome:
    name: str
    selector: str
    equity_curve: list[dict]   # same shape as existing BacktestResult.equity_curve
    trades: list[dict]         # same shape as existing BacktestResult.trades
    metrics: dict              # 7 keys: total_return_pct, annualized_return_pct,
                              # max_drawdown_pct, sharpe, profit_factor,
                              # num_trades, avg_holding_days

def run_backtest(
    *,
    spec: StrategySpec,
    shared: StrategyCompareShared,
    selector_fn: SelectorFn,
    panel: Any,                # pre-loaded DataFrame-like object
    rebalance_dates: list[date],
) -> BacktestOutcome: ...
```

The body of the existing `strategy_backtest` route is refactored into `run_backtest` so the route no longer embeds backtest logic. The existing route then becomes a thin wrapper that:

1. Calls `local_select(...)` (because the existing single-strategy route is hard-wired to that selector — see § 0 contract).
2. Calls `run_backtest(spec, shared, ...)` with a single `LocalSelectParams` spec.
3. Projects the `BacktestOutcome` into the existing `BacktestResult` schema and returns it.

This guarantees that `/backtest` and `/compare` execute byte-identical backtest code.

### 3.3 Parallel dispatch (`compare_engine.run_compare`)

```python
def run_compare(req: StrategyCompareRequest) -> StrategyCompareResponse:
    panel = load_panel_cached(req.shared.universe, req.shared.start_date, req.shared.end_date)
    rebars = _rebalance_dates(panel, req.shared.start_date, req.shared.end_date)

    def _one(spec: StrategySpec) -> BacktestOutcome:
        return run_backtest(
            spec=spec,
            shared=req.shared,
            selector_fn=resolve_selector(spec.selector),
            panel=panel,
            rebalance_dates=rebars,
        )

    with ThreadPoolExecutor(max_workers=len(req.strategies)) as pool:
        outcomes: list[BacktestOutcome] = list(pool.map(_one, req.strategies))

    return _align_and_respond(req, outcomes)
```

Notes:

- `pool.map` preserves input order, so `metrics[i]` and `curves[i]` align with `strategies[i]`.
- `max_workers = N` (≤ 4). The thread pool is created and torn down per request; FastAPI's event loop is never blocked.
- **No partial failure.** If any spec raises, the exception propagates out of `pool.map` and the whole request returns 500 with `detail.error == "spec_failed"` (see § 5.1). The wrapper catches and re-raises as `HTTPException`.

### 3.4 Intersection alignment (`_align_curves`)

```python
def _align_curves(outcomes: list[BacktestOutcome]) -> tuple[list[date], list[AlignedCurve], dict[str, int], float]:
    per = [{p["date"]: p for p in o.equity_curve} for o in outcomes]
    common = set.intersection(*(set(d.keys()) for d in per))
    common_dates = sorted(common)
    aligned = [
        AlignedCurve(
            name=o.name,
            points=[CurvePoint(date=d, **per[i][d]) for d in common_dates],
        )
        for i, o in enumerate(outcomes)
    ]
    dropped = {o.name: len(o.equity_curve) - len(aligned[i].points) for i, o in enumerate(outcomes)}
    coverage = len(common_dates) / (sum(len(o.equity_curve) for o in outcomes) / len(outcomes)) if outcomes else 0.0
    return common_dates, aligned, dropped, coverage
```

Decisions:

- Curves are **not** normalized to step-0 = 100. Each curve keeps its absolute `total_value` so the user can read the actual portfolio size at any point. The frontend may render a normalized view in a future iteration, but the wire format is absolute.
- `per_strategy_dropped` is exposed so the frontend can show "策略 A 原本 250 天, 对齐后剩 220 天".
- `coverage_ratio < 0.7` triggers a `warning: "low_coverage"` field on `AlignmentInfo` (the frontend renders a yellow banner above the chart).

### 3.5 Metric reuse

The 7 metric fields are computed by a private helper `_compute_metrics(equity_curve, trades)` extracted from `agent/src/ashare/strategies/backtest.py`. `run_backtest` calls it once and stores the result on `BacktestOutcome.metrics`. No new metric is added.

### 3.6 Module dependency graph (compare path)

```
routes.py:strategy_compare
  └── compare_engine.run_compare
        ├── local_loader.load_panel_cached
        ├── ThreadPoolExecutor.map
        │     └── compare_backtest.run_backtest
        │           ├── selector_fn(...)
        │           └── backtest._compute_metrics
        └── _align_curves
```

`api_server.py`, `docker-compose.yml`, and `StrategyPage.tsx` (in-flight rewrite) are not modified by this PR.

## § 4. `ComparePage` UI layout

Implementation files:

- `frontend/src/pages/ashare/ComparePage.tsx` (new, contains `StrategyCard` sub-component)
- `frontend/src/router.tsx` (+ 1 line)
- `frontend/src/pages/ashare/StrategyPage.tsx` (+ ~10 lines: a `<Link>` button + 2 imports; in-flight body untouched)

### 4.1 Route

```tsx
<Route path="/ashare/strategy/compare" element={<ComparePage />} />
```

Sibling of `/ashare/strategy`, not nested.

### 4.2 Page skeleton (top-to-bottom)

```
┌────────────────────────────────────────────────────────┐
│  SectionHeader: "策略对比"                              │
│  [← 返回策略页]   [运行对比]   [重置]                   │
├────────────────────────────────────────────────────────┤
│  Card 1: 共享参数 (默认展开)                           │
│    start_date, end_date, initial_cash,                 │
│    universe (Select), commission_bps, slippage_bps     │
├────────────────────────────────────────────────────────┤
│  Card 2: 策略卡区 (标题 "策略 (N/4)" + "+ 添加")       │
│    ┌──────────┐ ┌──────────┐                            │
│    │ Card A   │ │ Card B   │   2-column grid           │
│    │ name     │ │ name     │   3 or 4 cards -> 2×2      │
│    │ selector │ │ selector │                            │
│    │ top_n    │ │ top_n    │                            │
│    │ rebal    │ │ rebal    │                            │
│    │ [删除]   │ │ [删除]   │                            │
│    └──────────┘ └──────────┘                            │
├────────────────────────────────────────────────────────┤
│  Card 3: 结果区 (无结果时占位)                          │
│    - 覆盖度提示条 (coverage < 0.7)                     │
│    - 指标对比表 (SectionHeader + Table)                │
│    - 资金曲线 ECharts (SectionHeader + Chart)          │
└────────────────────────────────────────────────────────┘
```

Responsive:

- ≥ 1024px: 2-column strategy grid (2 cards side-by-side, 3–4 cards in 2×2).
- ≥ 768px: 1-column stacked.
- < 768px: 1-column stacked with compact padding.

### 4.3 Shared-params form

Plain HTML inputs / selects with `useState`. No new form library is added. Fields:

- `start_date` `<input type="date">` default `2025-01-01`
- `end_date` `<input type="date">` default `today` (frontend form default; server-side `date` type does not default)
- `initial_cash` `<input type="number" min=10000 step=10000>` default `1000000`
- `universe` `<select>` options `csi300 / csi500 / csi1000 / all_a` default `csi300`
- `commission_bps` `<input type="number" min=0 max=50 step=0.5>` default `3`
- `slippage_bps` `<input type="number" min=0 max=50 step=0.5>` default `5`

### 4.4 Strategy card

- `name` `<input type="text" maxlength=32>` default `"策略 ${i+1}"`
- `selector` `<select>` options `local_select / multi_factor` default `local_select`
- `local_select` branch: `top_n` (number 5–100, default 20), `rebalance_days` (number 1–60, default 5)
- `multi_factor` branch: same as above, plus a collapsible `factor_weights` JSON textarea (default empty)
- Delete button visible only when `strategies.length > 2`
- Card header color bar derived from a 4-color palette via `hash(name) % 4`. The same palette is used by the table columns and the ECharts series, so each strategy has a single consistent color across all three surfaces.

### 4.5 Add / delete

- "+ 添加" button enabled only when `strategies.length < 4`. Pushes a default card (`name = "策略 N"`, `selector = "local_select"`).
- "删除" button visible only when `strategies.length > 2`.
- No drag-reorder. (YAGNI.)

### 4.6 Results area

**a. Coverage banner** — rendered only when `alignment.coverage_ratio < 0.7`: "⚠ 对齐覆盖率 X%, 部分策略交易日被剔除, 结果仅供参考".

**b. Metric comparison table** — 10 rows × N columns:

| 指标 \ 策略 | 策略 A | 策略 B | ... |
| --- | --- | --- | --- |
| 区间 | 2025-01-01 ~ 2025-06-10 | ... | |
| 初始资金 | 1,000,000 | ... | |
| 终值 | 1,123,000 | ... | |
| 累计收益 | +12.30% | ... | |
| 年化收益 | +24.60% | ... | |
| 最大回撤 | -8.10% | ... | |
| Sharpe | 1.42 | ... | |
| 盈亏比 | 1.85 | ... | |
| 交易笔数 | 124 | ... | |
| 平均持仓天数 | 7.3 | ... | |

First column is sticky-left. Each strategy column uses the palette color. No "vs benchmark" column — that lands with the deferred attribution work.

**c. ECharts multi-line equity curve** — single chart, up to 4 series. X = `common_dates`; Y = `total_value`; tooltip shows that date's `total_value` + `drawdown_pct` for every series; legend uses palette swatches. Reuse the `useRef<HTMLDivElement>` + `useRef<echarts.EChartsType>` + `chart.setOption(...)` + `chart.dispose()` pattern from the in-flight `StrategyPage.tsx`. Do not extract a shared ECharts component (YAGNI).

### 4.7 Entry from `StrategyPage`

Add a single button next to the first `SectionHeader` of `StrategyPage.tsx`:

```tsx
<Link to="/ashare/strategy/compare">
  <Button variant="outline" size="sm">
    <BarChart3 className="w-4 h-4 mr-1" />
    打开策略对比
  </Button>
</Link>
```

Two new imports (`Link` from `react-router-dom`, `Button` from the project's existing button module, plus `BarChart3` from `lucide-react` which is already in the in-flight imports). In-flight body, types, and the HMR-unsafe `export default` shape are not modified.

### 4.8 Loading / error states

- Submit: button becomes "运行中…", form disabled. No hard `fetch` timeout on the client (server returns in < 5 min for typical intervals).
- 422: red alert at the top of the page with the first Pydantic error message; offending field gets a red border.
- 500: red alert with `detail.error` and `detail.detail` (or `detail.name` for spec-failed).
- Empty `num_trades`: that row in the table is greyed out, no error.

### 4.9 Reuse & new dependencies

- **0 new dependencies**. `echarts` is already imported in the in-flight `StrategyPage.tsx`; `lucide-react` is already in use.
- **1 new file**: `ComparePage.tsx` (with `StrategyCard` sub-component defined in the same file).
- **2 modified files**: `router.tsx` (+1 route), `StrategyPage.tsx` (+~10 lines).

## § 5. Error handling & observability

### 5.1 Error matrix

| Trigger | HTTP | Body | UI |
| --- | --- | --- | --- |
| `strategies` length ∉ [2, 4] | 422 | FastAPI default Pydantic | Top red alert, form-level error |
| Duplicate `strategies[].name` | 422 | Pydantic validator | Red border on the conflicting cards |
| `selector` not in `{local_select, multi_factor}` | 422 | Pydantic Literal | Red border on that card's `selector` field |
| `start_date >= end_date` or span > 5y | 422 | Pydantic | Red border on shared form dates |
| `initial_cash` out of [10k, 100M] | 422 | Pydantic | Red border on `initial_cash` |
| Unknown selector (registry miss) | 500 | `{ error: "unknown_selector", selector, available: [...] }` | Full-page red alert |
| A spec raises during backtest | 500 | `{ error: "spec_failed", name, selector, detail }` | Full-page red alert, user edits the named card and retries |
| `load_panel` raises (bad universe, parquet missing) | 500 | `{ error: "panel_load_failed", universe, detail }` | Full-page red alert, suggests changing universe or shrinking range |
| Compare succeeds but a spec has 0 trades | 200 | `metrics[i].num_trades = 0`, row greyed out | Not an error |

Single-spec failure → whole request 500 is **intentional**: "端到端" comparison requires all specs to complete. Partial response would mislead. This is locked in by § 3.3 ("no partial failure").

### 5.2 Exception-to-500 path

- `routes.py:strategy_compare` does not wrap calls in try/except chains.
- Internal `compare_engine.run_compare` catches `UnknownSelectorError` / spec exceptions / panel exceptions, wraps each into `HTTPException(status_code=500, detail={...})`, and re-raises.
- No global FastAPI exception handler is modified; behavior matches the rest of `routes.py`.

### 5.3 Logging

Use the project's existing `logging.getLogger(__name__)` (no new dependency).

```python
log = logging.getLogger(__name__)

def run_compare(req):
    log.info("strategy.compare.start shared=%s strategies=%d",
             req.shared.universe, len(req.strategies))
    t0 = time.monotonic()
    panel = load_panel_cached(...)
    outcomes = ...  # ThreadPoolExecutor.map
    common, aligned, dropped, coverage = _align_curves(outcomes)
    log.info(
        "strategy.compare.done elapsed_ms=%.1f common_dates=%d coverage=%.3f metrics=%s",
        (time.monotonic() - t0) * 1000, len(common), coverage,
        [(o.name, o.metrics["num_trades"]) for o in outcomes],
    )
```

Per-spec logs are at DEBUG level so the INFO stream stays compact. Operators can opt into DEBUG by `logging.getLogger("src.ashare.strategies.compare_engine").setLevel(DEBUG)`.

### 5.4 Observability fields

No self-reported metrics in the response (server-side logs are sufficient). The `alignment.coverage_ratio` and `per_strategy_dropped` fields are the user-visible quality signal. `elapsed_ms` is logged but not returned.

### 5.5 Metric field rounding & edge cases

| Field | Rounding | Edge case |
| --- | --- | --- |
| `total_value` | `round(x, 2)` | < 0 ⇒ raises (commission cannot drive account below zero in current model) |
| `total_return_pct` | `round(x, 2)` | — |
| `annualized_return_pct` | `round(x, 2)` | span < 30 days ⇒ `null` (annualization is meaningless); table renders as "n/a" |
| `max_drawdown_pct` | `round(x, 2)` | always ≤ 0 |
| `sharpe` | `round(x, 2)` | 0 trades ⇒ `0.0` |
| `profit_factor` | `round(x, 2)` | 0 trades ⇒ `0.0` |
| `num_trades` | not rounded | — |
| `avg_holding_days` | `round(x, 1)` | 0 trades ⇒ `0.0` |

**Non-finite defense**: `compare_backtest.run_backtest` applies `if not math.isfinite(x): x = 0.0` to every metric before it leaves the engine. This follows the pattern from commit `6107eaf fix(run-card): write strict JSON for non-finite metrics`. A dedicated test (`test_run_backtest_strict_json_for_nonfinite`) covers this.

### 5.6 Panel cache

`load_panel_cached(universe, start_date, end_date)` is wrapped in `functools.lru_cache(maxsize=8)`. Cache key is `(universe, start_date, end_date)` — `selector` and `params` do not affect the panel, so all N specs in a single `/compare` call share one panel load. `maxsize=8` bounds long-running memory.

### 5.7 Auth

`/ashare/strategy/compare` inherits the authn/authz posture of `/ashare/strategy/backtest`. No new permission tier. This is a research/backtest surface, not a broker action (see § 1 non-goal § 7).

### 5.8 Resource cleanup

- `ThreadPoolExecutor` is created via `with` and torn down after `pool.map` returns.
- ECharts instance is `dispose()`-d in the `useEffect` cleanup function, mirroring the in-flight `StrategyPage.tsx` pattern.
- DuckDB connection is **not** explicitly released — `local_loader` already manages connection reuse.

## § 6. Acceptance criteria & test plan

### 6.1 Unit tests (pytest) — new file `agent/tests/test_strategy_compare.py`

| Test ID | Covers | Input | Expectation |
| --- | --- | --- | --- |
| `test_registry_register_resolve` | registry happy path | register 2 fake selectors | `resolve_selector` returns the function; `UnknownSelectorError` on unknown name |
| `test_registry_duplicate_raises` | registry duplicate guard | two `@register_selector("dup")` | second raises `ValueError` |
| `test_compare_models_validator_strategies_length` | Pydantic edge | `[]` / `[s1]` / `[s1..s5]` | 422 |
| `test_compare_models_validator_unique_names` | Pydantic edge | two specs `name="A"` | 422 |
| `test_compare_models_validator_date_range` | Pydantic edge | `start_date == end_date` / span > 5y | 422 |
| `test_compare_models_validator_selector_params_match` | discriminated union | `selector="multi_factor"` with `LocalSelectParams` | 422 |
| `test_run_backtest_local_select_smoke` | `run_backtest` minimal | one `local_select` spec, 2024-01-01..2024-02-01, csi300 | `equity_curve` non-empty, all 7 metrics finite |
| `test_run_backtest_multi_factor_smoke` | `run_backtest` minimal | one `multi_factor` spec, same as above | same |
| `test_run_backtest_strict_json_for_nonfinite` | non-finite defense | spec engineered to trigger div-by-zero in `_compute_metrics` | every metric field `isfinite` |
| `test_run_compare_aligns_intersection` | alignment | two specs with different trade-date boundaries | `curves[0].points[*].date == curves[1].points[*].date`; `per_strategy_dropped` counts correct |
| `test_run_compare_metrics_order_matches_request` | output order | `[s1, s2, s3]` | `metrics[i].name == s_i.name` |
| `test_run_compare_threads_finish` | parallelism is real | 4 specs; inject 0.5s sleep in a fake selector | total wall time < 1.5s |
| `test_load_panel_cached_shared_within_call` | cache hit within one compare | 4 specs same universe + range | parquet read called exactly once (verified via `unittest.mock.patch`) |
| `test_route_strategy_compare_422_validation` | route 422 | FastAPI TestClient with bad body | 422 with expected field name |
| `test_route_strategy_compare_500_spec_failed` | route 500 spec-failed | selector that raises `RuntimeError` | 500 with `detail.error == "spec_failed"` and `detail.name` |
| `test_route_strategy_compare_500_unknown_selector` | route 500 unknown | spec `selector="bogus"` | 500 with `detail.error == "unknown_selector"` and `detail.available` |
| `test_route_strategy_compare_200_happy_path` | route 200 happy | two valid specs | 200, full schema, finite numbers |

**17 unit tests total**. None depend on real parquet; all substitute `load_panel_cached` with a fake panel (date index + price columns) via `unittest.mock.patch`.

### 6.2 End-to-end script — `scripts/run_strategy_compare_e2e.py`

Run manually (not in CI; depends on real parquet via `local_loader`):

```bash
python -m scripts.run_strategy_compare_e2e \
  --universe csi300 \
  --start 2025-01-01 --end 2025-03-01 \
  --strategies-json '[
    {"name":"local-baseline","selector":"local_select","params":{"top_n":20,"rebalance_days":5}},
    {"name":"mf-baseline","selector":"multi_factor","params":{"top_n":20,"rebalance_days":5}}
  ]'
```

Asserts:

- HTTP 200
- `len(metrics) == 2`
- `len(curves[0].points) == len(curves[1].points)` (intersection)
- ≥ 30 `common_dates` (interval is long enough)
- Prints coverage ratio, per-strategy `num_trades`, and Sharpe ranking.

Marked `[manual]` in PR template; "local OK" is a merge-gate comment, mirroring the existing `e2e_backtest` flow.

### 6.3 Frontend smoke (manual)

No new test framework added. PR description carries this checklist:

- [ ] `/ashare/strategy/compare` loads, no blank page
- [ ] Shared-params form: "+ 添加" / "删除" buttons enable/disable at the expected counts
- [ ] Submit: metric table renders 10 rows × N columns, columns in card order; ECharts multi-line equity curve renders
- [ ] `coverage_ratio < 0.7` triggers the yellow banner
- [ ] 422 / 500 produce red alerts as expected
- [ ] `StrategyPage` "打开策略对比" button navigates to `/ashare/strategy/compare`; the in-flight `StrategyPage` HMR warning is unchanged by this PR

### 6.4 Regression & build

```bash
git status --short --branch
git diff --check
python -m compileall -q agent/src/ashare/strategies/compare_engine.py \
                     agent/src/ashare/strategies/compare_backtest.py \
                     agent/src/ashare/strategies/selector_registry.py \
                     agent/src/ashare/strategies/compare_models.py
python -m py_compile agent/src/ashare/api/routes.py agent/api_server.py
pytest agent/tests/test_strategy_compare.py -q
pytest agent/tests/test_sdk_order_gate.py agent/tests/test_mandate_enforcement.py -q
cd frontend && npm ci && npm run build
```

The two pytest runs on `test_sdk_order_gate.py` and `test_mandate_enforcement.py` are required by `AGENT_CONTRIBUTOR_GUIDE.md` whenever `routes.py` is touched.

### 6.5 Tests explicitly excluded

- `e2e_backtest`
- `test_e2e_harness_v2.py`
- Browser automation / Playwright (UI surface is small; manual smoke is enough)
- `decision_tree`, `hypotheses`, `memory` (unrelated)

### 6.6 Done definition (PR merge gate)

1. All 17 unit tests in § 6.1 pass
2. All commands in § 6.4 pass
3. `cd frontend && npm run build` succeeds
4. § 6.2 e2e script runs locally; output or screenshot is attached to the PR
5. § 6.3 manual smoke checklist is ticked
6. `CHANGELOG.md` gains an entry in the style of `feat(strategy): add adaptive backtest + agent tools + strategy UI page`

## § 7. Out-of-scope follow-ups (parking lot)

These were considered and deferred:

- Difference attribution (B1c-i-2): returns `metrics[i] - metrics[j]` and per-day PnL decomposition.
- Overlap analysis (B1c-i-3): per-day top-N overlap rate and per-stock contribution to the equity delta.
- Persisted compare runs (B1c-async): `agent/runs`-landed, SSE-polled compare results, for N > 4 or intervals > 1y.
- New selectors (`trend_timing`, `wanrun_band`, `adaptive_backtest`): pluggable behind the registry; each brings its own `params` schema.
- Config-sweep compare (B1a) and factor-basket compare (B1b): these need a different request shape (parameter grid or factor-set id) and are not in this PR.
- Fix the in-flight `StrategyPage.tsx` HMR duplicate-default warning. Likely a Fast Refresh issue with the rewritten module's default export; needs its own PR.
