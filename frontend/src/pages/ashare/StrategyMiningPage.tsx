import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import * as echarts from "echarts";
import {
  BarChart3,
  ChevronLeft,
  RefreshCw,
  TrendingUp,
  Activity,
  Percent,
  Target,
  Zap,
  Calendar,
  Hash,
  Layers,
} from "lucide-react";
import { api, StrategyArtifactSummary } from "@/lib/api";
import { cn } from "@/lib/utils";
import { SectionHeader } from "@/components/common/SectionHeader";
import { MetricTile } from "@/components/common/MetricTile";

interface EquityPoint {
  date: string;
  equity: number;
}

const formatPct = (v?: number) =>
  typeof v === "number" ? `${v >= 0 ? "+" : ""}${v.toFixed(2)}%` : "—";

export default function StrategyMiningPage() {
  const [artifacts, setArtifacts] = useState<StrategyArtifactSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [equity, setEquity] = useState<EquityPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [detailKind, setDetailKind] = useState<"report" | "config" | "search" | "race">("report");

  const chartRef = useRef<HTMLDivElement | null>(null);
  const chartInstanceRef = useRef<echarts.EChartsType | null>(null);

  const loadList = async () => {
    setLoading(true);
    setError("");
    try {
      const list = await api.listStrategyArtifacts();
      setArtifacts(list);
      if (list.length > 0 && !selectedId) {
        setSelectedId(list[0].id);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadList();
  }, []);

  useEffect(() => {
    if (!selectedId) return;

    let cancelled = false;
    const loadDetail = async () => {
      try {
        const [data, equityData] = await Promise.all([
          api.getStrategyArtifact(selectedId, detailKind),
          detailKind === "report" ? api.getStrategyEquity(selectedId) : Promise.resolve({ equity_curve: [] }),
        ]);
        if (cancelled) return;
        setDetail(data);
        setEquity(equityData.equity_curve ?? []);
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    };
    loadDetail();
    return () => {
      cancelled = true;
    };
  }, [selectedId, detailKind]);

  useEffect(() => {
    if (!equity.length || !chartRef.current) return;

    if (!chartInstanceRef.current) {
      chartInstanceRef.current = echarts.init(chartRef.current);
    }

    const dates = equity.map((d) => d.date);
    const values = equity.map((d) => d.equity);

    chartInstanceRef.current.setOption({
      tooltip: { trigger: "axis" },
      grid: { left: 60, right: 40, top: 20, bottom: 40 },
      xAxis: { type: "category", data: dates, axisLabel: { rotate: 30 } },
      yAxis: { type: "value", name: "净值" },
      series: [
        {
          name: "策略净值",
          type: "line",
          data: values,
          smooth: true,
          showSymbol: false,
          itemStyle: { color: "#2563eb" },
          areaStyle: { color: "rgba(37, 99, 235, 0.1)" },
        },
      ],
    });
  }, [equity]);

  useEffect(() => {
    return () => {
      chartInstanceRef.current?.dispose();
      chartInstanceRef.current = null;
    };
  }, []);

  const selected = useMemo(
    () => artifacts.find((a) => a.id === selectedId) ?? null,
    [artifacts, selectedId]
  );

  const metrics = selected?.metrics ?? {};

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <SectionHeader
          icon={BarChart3}
          title="策略挖掘结果"
          meta={`${artifacts.length} 条已保存的策略记录`}
        />
        <div className="flex items-center gap-2">
          <Link
            to="/ashare/strategy"
            className="flex items-center gap-1.5 px-3 py-2 rounded-md border text-sm font-medium hover:bg-muted transition-colors"
          >
            <ChevronLeft className="h-4 w-4" />
            返回策略页
          </Link>
          <button
            onClick={loadList}
            disabled={loading}
            className="flex items-center gap-1.5 bg-primary text-primary-foreground px-4 py-2 rounded-md text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
          >
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            刷新
          </button>
        </div>
      </div>

      {error && (
        <div className="p-4 bg-red-50 text-red-700 rounded-md border border-red-200">{error}</div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* List */}
        <div className="lg:col-span-1 rounded-lg border bg-card shadow-sm overflow-hidden">
          <div className="p-3 border-b bg-muted/50 font-medium text-sm">策略列表</div>
          <div className="max-h-[70vh] overflow-y-auto">
            {artifacts.length === 0 && !loading && (
              <div className="p-6 text-sm text-muted-foreground text-center">
                暂无策略挖掘记录
              </div>
            )}
            {artifacts.map((a) => (
              <button
                key={a.id}
                onClick={() => {
                  setSelectedId(a.id);
                  setDetailKind("report");
                }}
                className={cn(
                  "w-full text-left p-3 border-b last:border-b-0 hover:bg-muted/50 transition-colors",
                  selectedId === a.id && "bg-primary/5 border-l-4 border-l-primary"
                )}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs text-muted-foreground">{a.id}</span>
                  <span className="text-xs text-muted-foreground">
                    {new Date(a.created_at).toLocaleString()}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap gap-2 text-xs">
                  <span className="px-1.5 py-0.5 rounded bg-muted">
                    年化 {formatPct(a.metrics?.annual_return_pct)}
                  </span>
                  <span className="px-1.5 py-0.5 rounded bg-muted">
                    Sharpe {typeof a.metrics?.sharpe === "number" ? a.metrics.sharpe.toFixed(2) : "—"}
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Detail */}
        <div className="lg:col-span-2 space-y-4">
          {!selected ? (
            <div className="rounded-lg border bg-card p-12 text-center text-muted-foreground">
              在左侧选择一条策略查看详情
            </div>
          ) : (
            <>
              {/* Kind tabs */}
              <div className="flex gap-2 border-b pb-2">
                {[
                  { key: "report", label: "报告" },
                  { key: "config", label: "配置" },
                  ...(selected.has_search ? [{ key: "search", label: "搜索摘要" }] : []),
                  ...(selected.has_race ? [{ key: "race", label: "赛马" }] : []),
                ].map((tab) => (
                  <button
                    key={tab.key}
                    onClick={() => setDetailKind(tab.key as typeof detailKind)}
                    className={cn(
                      "px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
                      detailKind === tab.key
                        ? "bg-primary text-primary-foreground"
                        : "text-muted-foreground hover:bg-muted hover:text-foreground"
                    )}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              {detailKind === "report" && (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <MetricTile
                      icon={TrendingUp}
                      label="年化收益"
                      value={formatPct(metrics.annual_return_pct)}
                      tone={(metrics.annual_return_pct ?? 0) >= 0 ? "text-green-600" : "text-red-600"}
                    />
                    <MetricTile
                      icon={BarChart3}
                      label="Sharpe"
                      value={typeof metrics.sharpe === "number" ? metrics.sharpe.toFixed(2) : "—"}
                    />
                    <MetricTile
                      icon={Percent}
                      label="最大回撤"
                      value={formatPct(metrics.max_drawdown_pct)}
                      tone="text-red-600"
                    />
                    <MetricTile
                      icon={Target}
                      label="Information Ratio"
                      value={typeof metrics.information_ratio === "number" ? metrics.information_ratio.toFixed(2) : "—"}
                    />
                    <MetricTile
                      icon={Zap}
                      label="换手率"
                      value={typeof metrics.turnover_approx === "number" ? metrics.turnover_approx.toFixed(2) : "—"}
                    />
                    <MetricTile
                      icon={Activity}
                      label="再平衡次数"
                      value={(detail?.n_rebalances as number) ?? "—"}
                    />
                    <MetricTile
                      icon={Calendar}
                      label="区间"
                      value={`${(detail?.period as string) ?? "—"}`}
                    />
                    <MetricTile
                      icon={Hash}
                      label="假设 ID"
                      value={selected.hypothesis_id.slice(0, 8)}
                    />
                  </div>

                  {equity.length > 0 && (
                    <div className="rounded-lg border bg-card p-4 shadow-sm">
                      <h3 className="text-sm font-medium mb-2">策略净值曲线</h3>
                      <div ref={chartRef} className="w-full h-80 rounded-md border bg-background" />
                    </div>
                  )}

                  <div className="rounded-lg border bg-card p-4 shadow-sm">
                    <h3 className="text-sm font-medium mb-3 flex items-center gap-2">
                      <Layers className="h-4 w-4" />
                      入选因子 ({selected.selected_alphas.length})
                    </h3>
                    <div className="flex flex-wrap gap-2">
                      {selected.selected_alphas.map((alpha) => (
                        <span
                          key={alpha}
                          className="px-2 py-1 rounded-md bg-muted text-xs font-mono"
                        >
                          {alpha}
                        </span>
                      ))}
                    </div>
                  </div>
                </>
              )}

              {(detailKind === "config" || detailKind === "search" || detailKind === "race") && detail && (
                <div className="rounded-lg border bg-card p-4 shadow-sm">
                  <h3 className="text-sm font-medium mb-2">
                    {detailKind === "config" && "策略配置"}
                    {detailKind === "search" && "搜索摘要"}
                    {detailKind === "race" && "赛马结果"}
                  </h3>
                  <pre className="text-xs bg-muted p-3 rounded-md overflow-auto max-h-[60vh]">
                    {JSON.stringify(detail, null, 2)}
                  </pre>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
