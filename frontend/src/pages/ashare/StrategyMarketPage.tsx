import { useEffect, useMemo, useRef, useState } from "react";
import {
  RefreshCw,
  Store,
  TrendingUp,
  Activity,
  BarChart3,
  DollarSign,
  Percent,
  Target,
  Zap,
  Clock,
  AlertCircle,
  Play,
  Layers,
} from "lucide-react";
import * as echarts from "echarts";
import { cn } from "@/lib/utils";
import { SectionHeader } from "@/components/common/SectionHeader";
import { MetricTile } from "@/components/common/MetricTile";
import { StockLink } from "@/components/common/StockLink";

const API_BASE = "";

interface StrategyParam {
  id: string;
  name: string;
  type: string;
  default: unknown;
  min?: number;
  max?: number;
  description?: string;
}

interface StrategyDef {
  id: string;
  name: string;
  description: string;
  category: string;
  params: StrategyParam[];
  supports_backtest: boolean;
  supports_realtime: boolean;
}

interface MatchedSymbol {
  symbol: string;
  name: string;
  signal: "buy" | "sell" | "hold" | "watch";
  score: number | null;
  confidence: number;
  rank: number | null;
  metadata: Record<string, unknown>;
}

interface StrategyMetrics {
  total_return_pct: number;
  annualized_return_pct: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
  win_rate: number;
  profit_factor: number;
  num_trades: number;
  avg_holding_days: number;
}

interface StrategySnapshot {
  strategy_id: string;
  run_at: string;
  status: "running" | "success" | "error" | "idle";
  market_date: string | null;
  matched: MatchedSymbol[];
  metrics: StrategyMetrics | null;
  backtest_curve: Array<{ date: string; value: number; drawdown_pct: number }> | null;
  error: string | null;
}

interface MarketState {
  strategies: StrategyDef[];
  snapshots: Record<string, StrategySnapshot>;
  last_updated: string | null;
}

const signalBadge: Record<MatchedSymbol["signal"], { label: string; cls: string }> = {
  buy: { label: "买入", cls: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400" },
  sell: { label: "卖出", cls: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400" },
  hold: { label: "持仓", cls: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400" },
  watch: { label: "观察", cls: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400" },
};

export default function StrategyMarketPage() {
  const [state, setState] = useState<MarketState>({ strategies: [], snapshots: {}, last_updated: null });
  const [activeId, setActiveId] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const chartRef = useRef<HTMLDivElement | null>(null);
  const chartInstanceRef = useRef<echarts.EChartsType | null>(null);

  const activeStrategy = useMemo(
    () => state.strategies.find((s) => s.id === activeId),
    [state.strategies, activeId]
  );
  const activeSnapshot = state.snapshots[activeId];

  const fetchState = async () => {
    try {
      const resp = await fetch(`${API_BASE}/ashare/strategy-market`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: MarketState = await resp.json();
      setState(data);
      if (!activeId && data.strategies.length > 0) {
        setActiveId(data.strategies[0].id);
      }
    } catch (e) {
      setError(String(e));
    }
  };

  const refreshAll = async () => {
    setLoading(true);
    setError("");
    try {
      const resp = await fetch(`${API_BASE}/ashare/strategy-market/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setState((prev) => ({
        ...prev,
        snapshots: { ...prev.snapshots, ...(data.snapshots || {}) },
        last_updated: new Date().toISOString(),
      }));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  // Initial load + polling
  useEffect(() => {
    fetchState();
    const id = setInterval(fetchState, 60000);
    return () => clearInterval(id);
  }, []);

  // SSE updates
  useEffect(() => {
    const es = new EventSource(`${API_BASE}/ashare/events`);
    es.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.event_type === "ashare_strategy_market" && msg.data?.snapshot) {
          const snap: StrategySnapshot = msg.data.snapshot;
          setState((prev) => ({
            ...prev,
            snapshots: { ...prev.snapshots, [snap.strategy_id]: snap },
            last_updated: new Date().toISOString(),
          }));
        }
      } catch {
        // ignore malformed SSE payloads
      }
    };
    return () => es.close();
  }, []);

  // Equity curve chart
  useEffect(() => {
    if (!activeSnapshot?.backtest_curve?.length || !chartRef.current) {
      chartInstanceRef.current?.dispose();
      chartInstanceRef.current = null;
      return;
    }
    if (!chartInstanceRef.current) {
      chartInstanceRef.current = echarts.init(chartRef.current);
    }
    const dates = activeSnapshot.backtest_curve.map((d) => d.date);
    const values = activeSnapshot.backtest_curve.map((d) => d.value);
    const drawdowns = activeSnapshot.backtest_curve.map((d) => -d.drawdown_pct);
    chartInstanceRef.current.setOption({
      tooltip: { trigger: "axis" },
      legend: { data: ["总资产", "回撤"], bottom: 0 },
      grid: { left: 60, right: 60, top: 20, bottom: 40 },
      xAxis: { type: "category", data: dates, axisLabel: { rotate: 30 } },
      yAxis: [
        { type: "value", name: "市值", position: "left" },
        { type: "value", name: "回撤%", position: "right" },
      ],
      series: [
        {
          name: "总资产",
          type: "line",
          data: values,
          smooth: true,
          showSymbol: false,
          itemStyle: { color: "#2563eb" },
          areaStyle: { color: "rgba(37, 99, 235, 0.1)" },
        },
        {
          name: "回撤",
          type: "line",
          yAxisIndex: 1,
          data: drawdowns,
          smooth: true,
          showSymbol: false,
          itemStyle: { color: "#dc2626" },
        },
      ],
    });
  }, [activeSnapshot]);

  useEffect(() => {
    return () => {
      chartInstanceRef.current?.dispose();
      chartInstanceRef.current = null;
    };
  }, []);

  const matchedCount = activeSnapshot?.matched?.length ?? 0;

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <SectionHeader icon={Store} title="策略市场" meta="实时策略结果、匹配标的与回测指标" />

      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="text-sm text-muted-foreground">
          共 {state.strategies.length} 个策略
          {state.last_updated && (
            <span className="ml-2">· 更新于 {new Date(state.last_updated).toLocaleString("zh-CN")}</span>
          )}
        </div>
        <button
          onClick={refreshAll}
          disabled={loading}
          className="flex items-center gap-2 bg-primary text-primary-foreground px-4 py-2 rounded-md text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
        >
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          {loading ? "刷新中..." : "刷新全部策略"}
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 dark:bg-red-900/20 dark:border-red-800 p-3 flex items-center gap-2 text-sm text-red-700 dark:text-red-300">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      {/* Strategy cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4">
        {state.strategies.map((s) => {
          const snap = state.snapshots[s.id];
          const statusColor =
            snap?.status === "success"
              ? "bg-green-500"
              : snap?.status === "running"
              ? "bg-amber-500"
              : snap?.status === "error"
              ? "bg-red-500"
              : "bg-muted";
          return (
            <button
              key={s.id}
              onClick={() => setActiveId(s.id)}
              className={cn(
                "text-left rounded-lg border p-4 transition-shadow hover:shadow-md",
                activeId === s.id ? "ring-2 ring-primary border-primary" : "bg-card"
              )}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-muted-foreground">{s.category}</span>
                <span className={cn("h-2 w-2 rounded-full", statusColor)} />
              </div>
              <h3 className="font-semibold text-sm">{s.name}</h3>
              <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{s.description}</p>
              <div className="mt-3 text-xs text-muted-foreground">
                匹配标的: <span className="font-medium text-foreground">{snap?.matched?.length ?? 0}</span>
              </div>
            </button>
          );
        })}
      </div>

      {/* Active strategy detail */}
      {activeStrategy && (
        <div className="space-y-4">
          <div className="rounded-lg border bg-card shadow-sm p-4">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h2 className="text-lg font-semibold flex items-center gap-2">
                  <Play className="h-5 w-5 text-primary" />
                  {activeStrategy.name}
                </h2>
                <p className="text-sm text-muted-foreground mt-1">{activeStrategy.description}</p>
                {activeSnapshot?.market_date && (
                  <p className="text-xs text-muted-foreground mt-2">
                    市场日期 {activeSnapshot.market_date}
                    {activeSnapshot.run_at && (
                      <span className="ml-2">· 运行 {new Date(activeSnapshot.run_at).toLocaleString("zh-CN")}</span>
                    )}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2">
                <span
                  className={cn(
                    "px-2 py-1 rounded-md text-xs font-medium",
                    activeSnapshot?.status === "success"
                      ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                      : activeSnapshot?.status === "running"
                      ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
                      : activeSnapshot?.status === "error"
                      ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                      : "bg-muted text-muted-foreground"
                  )}
                >
                  {activeSnapshot?.status ?? "idle"}
                </span>
              </div>
            </div>
          </div>

          {activeSnapshot?.error && (
            <div className="rounded-lg border border-red-200 bg-red-50 dark:bg-red-900/20 dark:border-red-800 p-3 text-sm text-red-700 dark:text-red-300">
              {activeSnapshot.error}
            </div>
          )}

          {activeSnapshot?.metrics && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <MetricTile
                icon={DollarSign}
                label="总收益"
                value={`${activeSnapshot.metrics.total_return_pct.toFixed(2)}%`}
                tone={activeSnapshot.metrics.total_return_pct >= 0 ? "text-green-600" : "text-red-600"}
              />
              <MetricTile icon={BarChart3} label="夏普比率" value={activeSnapshot.metrics.sharpe_ratio.toFixed(2)} />
              <MetricTile
                icon={Percent}
                label="最大回撤"
                value={`${activeSnapshot.metrics.max_drawdown_pct.toFixed(2)}%`}
                tone="text-red-600"
              />
              <MetricTile icon={Target} label="胜率" value={`${activeSnapshot.metrics.win_rate.toFixed(1)}%`} />
              <MetricTile
                icon={TrendingUp}
                label="年化收益"
                value={`${activeSnapshot.metrics.annualized_return_pct.toFixed(2)}%`}
              />
              <MetricTile icon={Zap} label="盈亏比" value={activeSnapshot.metrics.profit_factor.toFixed(2)} />
              <MetricTile icon={Clock} label="交易次数" value={activeSnapshot.metrics.num_trades} />
              <MetricTile
                icon={Activity}
                label="平均持仓"
                value={`${activeSnapshot.metrics.avg_holding_days.toFixed(1)}天`}
              />
            </div>
          )}

          {activeSnapshot?.backtest_curve && activeSnapshot.backtest_curve.length > 0 && (
            <div className="rounded-lg border bg-card shadow-sm p-4">
              <h3 className="text-sm font-medium mb-2 flex items-center gap-2">
                <Layers className="h-4 w-4" />
                收益曲线
              </h3>
              <div ref={chartRef} className="w-full h-80 rounded-md border bg-background" />
            </div>
          )}

          <div className="rounded-lg border bg-card shadow-sm overflow-hidden">
            <div className="p-4 border-b flex items-center justify-between">
              <h3 className="text-sm font-medium flex items-center gap-2">
                <Activity className="h-4 w-4" />
                匹配标的
              </h3>
              <span className="text-xs text-muted-foreground">共 {matchedCount} 只</span>
            </div>
            {matchedCount > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-muted/50">
                    <tr>
                      <th className="px-4 py-2 text-left font-medium">排名</th>
                      <th className="px-4 py-2 text-left font-medium">代码</th>
                      <th className="px-4 py-2 text-left font-medium">名称</th>
                      <th className="px-4 py-2 text-center font-medium">信号</th>
                      <th className="px-4 py-2 text-right font-medium">得分</th>
                      <th className="px-4 py-2 text-right font-medium">置信度</th>
                      <th className="px-4 py-2 text-left font-medium">详情</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activeSnapshot.matched.map((m, idx) => (
                      <tr key={m.symbol} className="border-b last:border-b-0 hover:bg-muted/30">
                        <td className="px-4 py-2 text-muted-foreground">{m.rank ?? idx + 1}</td>
                        <td className="px-4 py-2 font-mono font-medium">
                          <StockLink symbol={m.symbol} />
                        </td>
                        <td className="px-4 py-2">
                          <StockLink symbol={m.symbol}>{m.name || "—"}</StockLink>
                        </td>
                        <td className="px-4 py-2 text-center">
                          <span className={cn("px-2 py-0.5 rounded text-xs font-medium", signalBadge[m.signal].cls)}>
                            {signalBadge[m.signal].label}
                          </span>
                        </td>
                        <td className="px-4 py-2 text-right">{m.score?.toFixed(3) ?? "—"}</td>
                        <td className="px-4 py-2 text-right">{(m.confidence * 100).toFixed(1)}%</td>
                        <td className="px-4 py-2 text-xs text-muted-foreground">
                          {Object.entries(m.metadata)
                            .slice(0, 4)
                            .map(([k, v]) => `${k}: ${typeof v === "number" ? v.toFixed(2) : String(v)}`)
                            .join(" · ")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="p-8 text-center text-sm text-muted-foreground">
                暂无匹配标的，点击右上角刷新全部策略。
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
