import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowLeft,
  BarChart3,
  Plus,
  Trash2,
  TrendingUp,
  AlertTriangle,
} from "lucide-react";
import * as echarts from "echarts";
import { cn } from "@/lib/utils";
import { SectionHeader } from "@/components/common/SectionHeader";

const API_BASE = "";

const PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#9333ea"];

interface SharedParams {
  start_date: string;
  end_date: string;
  initial_cash: number;
  universe: string;
  commission_bps: number;
  slippage_bps: number;
}

interface StrategyParams {
  top_n: number;
  rebalance_days: number;
  factor_weights?: string;
}

interface StrategyCardState {
  name: string;
  selector: "local_select" | "multi_factor";
  params: StrategyParams;
}

interface CurvePoint {
  date: string;
  total_value: number;
  drawdown_pct: number;
  num_positions: number;
}

interface AlignedCurve {
  name: string;
  points: CurvePoint[];
}

interface StrategyMetric {
  name: string;
  selector: string;
  start_date: string;
  end_date: string;
  initial_cash: number;
  final_value: number;
  total_return_pct: number;
  annualized_return_pct: number | null;
  max_drawdown_pct: number;
  sharpe: number;
  profit_factor: number;
  num_trades: number;
  avg_holding_days: number;
}

interface CompareResponse {
  shared: SharedParams;
  alignment: {
    common_dates: string[];
    per_strategy_dropped: Record<string, number>;
    coverage_ratio: number;
    warning: "low_coverage" | null;
  };
  metrics: StrategyMetric[];
  curves: AlignedCurve[];
}

const UNIVERSE_OPTIONS = ["csi300", "csi500", "csi1000", "all_a"];

function todayStr() {
  return new Date().toISOString().split("T")[0];
}

function colorForName(name: string) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  return PALETTE[Math.abs(hash) % PALETTE.length];
}

function defaultStrategyCard(index: number): StrategyCardState {
  return {
    name: `策略 ${index + 1}`,
    selector: "local_select",
    params: {
      top_n: 20,
      rebalance_days: 5,
      factor_weights: "",
    },
  };
}

function formatPct(value: number | null | undefined) {
  if (value === null || value === undefined) return "n/a";
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatNumber(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("zh-CN");
}

export default function ComparePage() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [result, setResult] = useState<CompareResponse | null>(null);

  const [shared, setShared] = useState<SharedParams>({
    start_date: "2025-01-01",
    end_date: todayStr(),
    initial_cash: 1_000_000,
    universe: "csi300",
    commission_bps: 3,
    slippage_bps: 5,
  });

  const [strategies, setStrategies] = useState<StrategyCardState[]>([
    defaultStrategyCard(0),
    defaultStrategyCard(1),
  ]);

  const chartRef = useRef<HTMLDivElement | null>(null);
  const chartInstanceRef = useRef<echarts.EChartsType | null>(null);

  const updateStrategy = (index: number, patch: Partial<StrategyCardState>) => {
    setStrategies((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], ...patch };
      return next;
    });
  };

  const updateStrategyParams = (
    index: number,
    patch: Partial<StrategyParams>
  ) => {
    setStrategies((prev) => {
      const next = [...prev];
      next[index] = {
        ...next[index],
        params: { ...next[index].params, ...patch },
      };
      return next;
    });
  };

  const addStrategy = () => {
    setStrategies((prev) => [...prev, defaultStrategyCard(prev.length)]);
  };

  const removeStrategy = (index: number) => {
    setStrategies((prev) => prev.filter((_, i) => i !== index));
  };

  const reset = () => {
    setShared({
      start_date: "2025-01-01",
      end_date: todayStr(),
      initial_cash: 1_000_000,
      universe: "csi300",
      commission_bps: 3,
      slippage_bps: 5,
    });
    setStrategies([defaultStrategyCard(0), defaultStrategyCard(1)]);
    setResult(null);
    setError("");
    setFieldErrors({});
  };

  const buildPayload = () => {
    return {
      shared,
      strategies: strategies.map((s) => {
        const params: Record<string, unknown> = {
          top_n: s.params.top_n,
          rebalance_days: s.params.rebalance_days,
        };
        if (s.selector === "multi_factor" && s.params.factor_weights?.trim()) {
          try {
            params.factor_weights = JSON.parse(s.params.factor_weights);
          } catch {
            // ignore invalid JSON
          }
        }
        return {
          name: s.name,
          selector: s.selector,
          params,
        };
      }),
    };
  };

  const runCompare = async () => {
    setLoading(true);
    setError("");
    setFieldErrors({});
    try {
      const resp = await fetch(`${API_BASE}/ashare/strategy/compare`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildPayload()),
      });
      const data = await resp.json();
      if (!resp.ok) {
        if (resp.status === 422 && Array.isArray(data.detail)) {
          const first = data.detail[0];
          setError(`校验失败: ${first.loc?.join(".") || ""} ${first.msg}`);
          const fields: Record<string, string> = {};
          for (const err of data.detail) {
            const path = err.loc?.join(".") || "";
            fields[path] = err.msg;
          }
          setFieldErrors(fields);
        } else if (data.detail?.error) {
          setError(`${data.detail.error}: ${data.detail.detail || data.detail.name || ""}`);
        } else {
          setError(`HTTP ${resp.status}: ${JSON.stringify(data)}`);
        }
        setLoading(false);
        return;
      }
      setResult(data);
    } catch (e) {
      setError(String(e));
    }
    setLoading(false);
  };

  // Render / update ECharts multi-line equity curve.
  useEffect(() => {
    if (!result?.curves?.length || !chartRef.current) return;

    if (!chartInstanceRef.current) {
      chartInstanceRef.current = echarts.init(chartRef.current);
    }

    const dates = result.alignment.common_dates;
    const series = result.curves.map((curve) => ({
      name: curve.name,
      type: "line",
      data: curve.points.map((p) => p.total_value),
      smooth: true,
      showSymbol: false,
      itemStyle: { color: colorForName(curve.name) },
      lineStyle: { width: 2 },
    }));

    chartInstanceRef.current.setOption({
      tooltip: {
        trigger: "axis",
        formatter: (params: any) => {
          const date = params?.[0]?.axisValue;
          const rows = params.map((p: any) => {
            const curve = result.curves.find((c) => c.name === p.seriesName);
            const point = curve?.points[p.dataIndex];
            return `${p.marker} ${p.seriesName}: ${Number(p.value).toLocaleString(
              "zh-CN"
            )} (回撤 ${point ? -point.drawdown_pct : 0}%)`;
          });
          return `<div class="font-medium">${date}</div>${rows.join("<br/>")}`;
        },
      },
      legend: {
        data: result.curves.map((c) => c.name),
        bottom: 0,
      },
      grid: { left: 60, right: 40, top: 20, bottom: 40 },
      xAxis: {
        type: "category",
        data: dates,
        axisLabel: { rotate: 30 },
      },
      yAxis: {
        type: "value",
        name: "总资产",
        axisLabel: {
          formatter: (value: number) =>
            value >= 1_000_000
              ? `${(value / 1_000_000).toFixed(1)}M`
              : value.toLocaleString("zh-CN"),
        },
      },
      series,
    });
  }, [result]);

  // Dispose chart on unmount.
  useEffect(() => {
    return () => {
      chartInstanceRef.current?.dispose();
      chartInstanceRef.current = null;
    };
  }, []);

  const metricRows = useMemo(
    () => [
      { key: "range", label: "区间", render: (m: StrategyMetric) => `${m.start_date} ~ ${m.end_date}` },
      { key: "initial_cash", label: "初始资金", render: (m: StrategyMetric) => formatNumber(m.initial_cash) },
      { key: "final_value", label: "终值", render: (m: StrategyMetric) => formatNumber(m.final_value) },
      { key: "total_return_pct", label: "累计收益", render: (m: StrategyMetric) => formatPct(m.total_return_pct) },
      { key: "annualized_return_pct", label: "年化收益", render: (m: StrategyMetric) => formatPct(m.annualized_return_pct) },
      { key: "max_drawdown_pct", label: "最大回撤", render: (m: StrategyMetric) => formatPct(m.max_drawdown_pct) },
      { key: "sharpe", label: "Sharpe", render: (m: StrategyMetric) => m.sharpe.toFixed(2) },
      { key: "profit_factor", label: "盈亏比", render: (m: StrategyMetric) => m.profit_factor.toFixed(2) },
      { key: "num_trades", label: "交易笔数", render: (m: StrategyMetric) => m.num_trades.toString() },
      { key: "avg_holding_days", label: "平均持仓天数", render: (m: StrategyMetric) => m.avg_holding_days.toFixed(1) },
    ],
    []
  );

  const hasError = (path: string) => !!fieldErrors[path];

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <SectionHeader
          icon={BarChart3}
          title="策略对比"
          meta="多策略并行回测 + 资金曲线对齐"
        />
        <div className="flex flex-wrap items-center gap-2">
          <Link to="/ashare/strategy">
            <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-md border text-sm font-medium hover:bg-muted transition-colors">
              <ArrowLeft className="w-4 h-4" />
              返回策略页
            </button>
          </Link>
          <button
            onClick={runCompare}
            disabled={loading}
            className="flex items-center gap-1.5 bg-primary text-primary-foreground px-4 py-1.5 rounded-md text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
          >
            <TrendingUp className="w-4 h-4" />
            {loading ? "运行中…" : "运行对比"}
          </button>
          <button
            onClick={reset}
            disabled={loading}
            className="px-3 py-1.5 rounded-md border text-sm font-medium hover:bg-muted disabled:opacity-50"
          >
            重置
          </button>
        </div>
      </div>

      {error && (
        <div className="p-4 bg-red-50 text-red-700 rounded-md border border-red-200 text-sm">
          {error}
        </div>
      )}

      {/* Shared params card */}
      <div className="rounded-lg border bg-card shadow-sm">
        <div className="p-4 border-b">
          <h3 className="font-medium text-sm">共享参数</h3>
        </div>
        <div className="p-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <div>
            <label className="block text-xs text-muted-foreground mb-1">开始日期</label>
            <input
              type="date"
              value={shared.start_date}
              onChange={(e) => setShared({ ...shared, start_date: e.target.value })}
              disabled={loading}
              className={cn(
                "border rounded-md px-3 py-1.5 text-sm bg-background w-full",
                hasError("shared.start_date") && "border-red-500"
              )}
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">结束日期</label>
            <input
              type="date"
              value={shared.end_date}
              onChange={(e) => setShared({ ...shared, end_date: e.target.value })}
              disabled={loading}
              className={cn(
                "border rounded-md px-3 py-1.5 text-sm bg-background w-full",
                hasError("shared.end_date") && "border-red-500"
              )}
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">初始资金</label>
            <input
              type="number"
              min={10000}
              step={10000}
              value={shared.initial_cash}
              onChange={(e) =>
                setShared({ ...shared, initial_cash: Number(e.target.value) })
              }
              disabled={loading}
              className={cn(
                "border rounded-md px-3 py-1.5 text-sm bg-background w-full",
                hasError("shared.initial_cash") && "border-red-500"
              )}
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">股票池</label>
            <select
              value={shared.universe}
              onChange={(e) => setShared({ ...shared, universe: e.target.value })}
              disabled={loading}
              className="border rounded-md px-3 py-1.5 text-sm bg-background w-full"
            >
              {UNIVERSE_OPTIONS.map((u) => (
                <option key={u} value={u}>
                  {u}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">佣金 (bps)</label>
            <input
              type="number"
              min={0}
              max={50}
              step={0.5}
              value={shared.commission_bps}
              onChange={(e) =>
                setShared({ ...shared, commission_bps: Number(e.target.value) })
              }
              disabled={loading}
              className="border rounded-md px-3 py-1.5 text-sm bg-background w-full"
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">滑点 (bps)</label>
            <input
              type="number"
              min={0}
              max={50}
              step={0.5}
              value={shared.slippage_bps}
              onChange={(e) =>
                setShared({ ...shared, slippage_bps: Number(e.target.value) })
              }
              disabled={loading}
              className="border rounded-md px-3 py-1.5 text-sm bg-background w-full"
            />
          </div>
        </div>
      </div>

      {/* Strategy cards */}
      <div className="rounded-lg border bg-card shadow-sm">
        <div className="p-4 border-b flex items-center justify-between">
          <h3 className="font-medium text-sm">策略 ({strategies.length}/4)</h3>
          <button
            onClick={addStrategy}
            disabled={strategies.length >= 4 || loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md border text-sm font-medium hover:bg-muted disabled:opacity-50"
          >
            <Plus className="w-4 h-4" />
            添加
          </button>
        </div>
        <div className="p-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
          {strategies.map((s, idx) => {
            const color = colorForName(s.name);
            return (
              <div
                key={idx}
                className="rounded-lg border bg-background overflow-hidden"
              >
                <div
                  className="h-1.5"
                  style={{ backgroundColor: color }}
                />
                <div className="p-4 space-y-3">
                  <div className="flex items-center justify-between gap-2">
                    <input
                      type="text"
                      maxLength={32}
                      value={s.name}
                      onChange={(e) => updateStrategy(idx, { name: e.target.value })}
                      disabled={loading}
                      className={cn(
                        "border rounded-md px-3 py-1.5 text-sm font-medium bg-background flex-1",
                        hasError(`strategies.${idx}.name`) && "border-red-500"
                      )}
                    />
                    {strategies.length > 2 && (
                      <button
                        onClick={() => removeStrategy(idx)}
                        disabled={loading}
                        className="p-1.5 rounded-md text-muted-foreground hover:text-red-600 hover:bg-red-50 disabled:opacity-50"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    )}
                  </div>

                  <div>
                    <label className="block text-xs text-muted-foreground mb-1">选股器</label>
                    <select
                      value={s.selector}
                      onChange={(e) =>
                        updateStrategy(idx, {
                          selector: e.target.value as StrategyCardState["selector"],
                        })
                      }
                      disabled={loading}
                      className={cn(
                        "border rounded-md px-3 py-1.5 text-sm bg-background w-full",
                        hasError(`strategies.${idx}.selector`) && "border-red-500"
                      )}
                    >
                      <option value="local_select">local_select</option>
                      <option value="multi_factor">multi_factor</option>
                    </select>
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs text-muted-foreground mb-1">top_n</label>
                      <input
                        type="number"
                        min={5}
                        max={100}
                        value={s.params.top_n}
                        onChange={(e) =>
                          updateStrategyParams(idx, { top_n: Number(e.target.value) })
                        }
                        disabled={loading}
                        className="border rounded-md px-3 py-1.5 text-sm bg-background w-full"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-muted-foreground mb-1">再平衡 (天)</label>
                      <input
                        type="number"
                        min={1}
                        max={60}
                        value={s.params.rebalance_days}
                        onChange={(e) =>
                          updateStrategyParams(idx, { rebalance_days: Number(e.target.value) })
                        }
                        disabled={loading}
                        className="border rounded-md px-3 py-1.5 text-sm bg-background w-full"
                      />
                    </div>
                  </div>

                  {s.selector === "multi_factor" && (
                    <div>
                      <label className="block text-xs text-muted-foreground mb-1">
                        factor_weights (JSON, 可选)
                      </label>
                      <textarea
                        value={s.params.factor_weights || ""}
                        onChange={(e) =>
                          updateStrategyParams(idx, { factor_weights: e.target.value })
                        }
                        disabled={loading}
                        rows={2}
                        placeholder='{"gtja191_120": 0.4}'
                        className="border rounded-md px-3 py-1.5 text-sm bg-background w-full font-mono"
                      />
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Results */}
      {result && (
        <div className="space-y-6">
          {result.alignment.warning === "low_coverage" && (
            <div className="p-4 bg-yellow-50 text-yellow-800 rounded-md border border-yellow-200 text-sm flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <div>
                ⚠ 对齐覆盖率 {Math.round(result.alignment.coverage_ratio * 100)}%,
                部分策略交易日被剔除, 结果仅供参考
              </div>
            </div>
          )}

          {/* Metric table */}
          <div className="rounded-lg border bg-card shadow-sm overflow-hidden">
            <div className="p-4 border-b">
              <h3 className="font-medium text-sm">指标对比</h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium sticky left-0 bg-muted/50 z-10">指标 \ 策略</th>
                    {result.metrics.map((m) => (
                      <th
                        key={m.name}
                        className="px-3 py-2 text-left font-medium whitespace-nowrap"
                        style={{ color: colorForName(m.name) }}
                      >
                        {m.name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {metricRows.map((row) => (
                    <tr key={row.key} className="border-b last:border-b-0">
                      <td className="px-3 py-2 font-medium text-muted-foreground sticky left-0 bg-card z-10">
                        {row.label}
                      </td>
                      {result.metrics.map((m) => (
                        <td
                          key={m.name}
                          className={cn(
                            "px-3 py-2 whitespace-nowrap",
                            row.key === "num_trades" && m.num_trades === 0 && "text-muted-foreground"
                          )}
                        >
                          {row.render(m)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Equity curve */}
          <div className="rounded-lg border bg-card shadow-sm">
            <div className="p-4 border-b">
              <h3 className="font-medium text-sm">资金曲线</h3>
            </div>
            <div className="p-4">
              <div
                ref={chartRef}
                className="w-full h-96 rounded-md border bg-background"
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
