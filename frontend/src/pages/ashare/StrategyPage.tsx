import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { TrendingUp, BarChart3, Activity, DollarSign, Percent, Clock, Target, Zap, LineChart, Search, Store } from "lucide-react";
import * as echarts from "echarts";
import { cn } from "@/lib/utils";
import { SectionHeader } from "@/components/common/SectionHeader";
import { MetricTile } from "@/components/common/MetricTile";

const API_BASE = "";

interface StockPick {
  symbol: string;
  composite_score: number;
  momentum_20d: number;
  volume_ratio: number;
  ma5: number;
  ma20: number;
  ma60: number;
  atr_14?: number;
}

interface BacktestResult {
  start_date: string;
  end_date: string;
  initial_cash: number;
  final_value: number;
  total_return_pct: number;
  annualized_return_pct: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
  win_rate: number;
  profit_factor: number;
  num_trades: number;
  avg_holding_days: number;
  equity_curve?: Array<{ date: string; total_value: number; drawdown_pct: number; num_positions: number }>;
  trades?: Array<{
    date: string;
    symbol: string;
    action: "buy" | "sell";
    price: number;
    quantity: number;
    pnl_pct?: number;
    days_held?: number;
    reason?: string;
  }>;
}

type TabKey = "select" | "backtest" | "profile";

export default function StrategyPage() {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<TabKey>("select");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Tab-scoped results so switching tabs preserves context.
  const [selectResult, setSelectResult] = useState<any>(null);
  const [backtestResult, setBacktestResult] = useState<BacktestResult | null>(null);
  const [profileResult, setProfileResult] = useState<any>(null);

  // Selection params
  const [tradeDate, setTradeDate] = useState(new Date().toISOString().split("T")[0]);
  const [topN, setTopN] = useState(20);

  // Backtest params
  const [startDate, setStartDate] = useState("2025-01-01");
  const [endDate, setEndDate] = useState("2025-06-10");
  const [initialCash, setInitialCash] = useState(1_000_000);

  // Profile params
  const [profileSymbol, setProfileSymbol] = useState("000001.SZ");

  const chartRef = useRef<HTMLDivElement | null>(null);
  const chartInstanceRef = useRef<echarts.EChartsType | null>(null);

  const runSelection = async () => {
    setLoading(true);
    setError("");
    try {
      const resp = await fetch(
        `${API_BASE}/ashare/strategy/select?trade_date=${tradeDate}&top_n=${topN}`
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setSelectResult(data);
    } catch (e) {
      setError(String(e));
    }
    setLoading(false);
  };

  const runBacktest = async () => {
    setLoading(true);
    setError("");
    try {
      const resp = await fetch(`${API_BASE}/ashare/strategy/backtest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start_date: startDate,
          end_date: endDate,
          initial_cash: initialCash,
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setBacktestResult(data);
    } catch (e) {
      setError(String(e));
    }
    setLoading(false);
  };

  const runProfile = async () => {
    setLoading(true);
    setError("");
    try {
      const resp = await fetch(
        `${API_BASE}/ashare/strategy/profile?symbol=${profileSymbol}`
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setProfileResult(data);
    } catch (e) {
      setError(String(e));
    }
    setLoading(false);
  };

  // Render equity curve chart when backtest result changes.
  useEffect(() => {
    if (!backtestResult?.equity_curve?.length || !chartRef.current) return;

    if (!chartInstanceRef.current) {
      chartInstanceRef.current = echarts.init(chartRef.current);
    }

    const dates = backtestResult.equity_curve.map((d) => d.date);
    const values = backtestResult.equity_curve.map((d) => d.total_value);
    const drawdowns = backtestResult.equity_curve.map((d) => -d.drawdown_pct);

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
  }, [backtestResult]);

  // Dispose chart on unmount.
  useEffect(() => {
    return () => {
      chartInstanceRef.current?.dispose();
      chartInstanceRef.current = null;
    };
  }, []);

  const buyTrades = useMemo(
    () => backtestResult?.trades?.filter((t) => t.action === "buy") ?? [],
    [backtestResult]
  );
  const sellTrades = useMemo(
    () => backtestResult?.trades?.filter((t) => t.action === "sell") ?? [],
    [backtestResult]
  );

  const tabs = [
    { key: "select", label: "多因子选股", icon: Search },
    { key: "backtest", label: "策略回测", icon: LineChart },
    { key: "profile", label: "个股画像", icon: Activity },
  ] as const;

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <SectionHeader
          icon={TrendingUp}
          title="A股量化策略"
          meta="多因子选股 + 自适应回测 + 个股画像"
        />
        <button
          onClick={() => navigate("/ashare/strategy/market")}
          className="flex items-center gap-2 bg-primary text-primary-foreground px-4 py-2 rounded-md text-sm font-medium hover:bg-primary/90"
        >
          <Store className="h-4 w-4" />
          进入策略市场
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b pb-2">
        {tabs.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setActiveTab(key)}
            className={cn(
              "flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors",
              activeTab === key
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            )}
          >
            <Icon className="h-4 w-4" />
            {label}
          </button>
        ))}
      </div>

      {/* Selection Panel */}
      {activeTab === "select" && (
        <div className="rounded-lg border bg-card shadow-sm">
          <div className="p-4 flex flex-wrap items-end gap-4">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">交易日期</label>
              <input
                type="date"
                value={tradeDate}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setTradeDate(e.target.value)}
                className="border rounded-md px-3 py-1.5 text-sm bg-background"
              />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">选股数量</label>
              <input
                type="number"
                value={topN}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setTopN(Number(e.target.value))}
                className="border rounded-md px-3 py-1.5 text-sm w-24 bg-background"
              />
            </div>
            <button
              onClick={runSelection}
              disabled={loading}
              className="bg-primary text-primary-foreground px-4 py-1.5 rounded-md text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
            >
              {loading ? "运行中..." : "执行选股"}
            </button>
          </div>

          {selectResult && (
            <div className="border-t p-4">
              <p className="text-sm text-muted-foreground mb-3">
                为 <span className="font-semibold text-foreground">{selectResult.trade_date}</span> 选出{" "}
                <span className="font-semibold text-foreground">{selectResult.selected_count}</span> 只股票
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-muted/50">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium">代码</th>
                      <th className="px-3 py-2 text-right font-medium">综合得分</th>
                      <th className="px-3 py-2 text-right font-medium">20日动量</th>
                      <th className="px-3 py-2 text-right font-medium">量比</th>
                      <th className="px-3 py-2 text-right font-medium">MA5</th>
                      <th className="px-3 py-2 text-right font-medium">MA20</th>
                      <th className="px-3 py-2 text-right font-medium">MA60</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selectResult.stocks.map((s: StockPick) => (
                      <tr key={s.symbol} className="border-b last:border-b-0 hover:bg-muted/30">
                        <td className="px-3 py-2 font-mono">{s.symbol}</td>
                        <td className="px-3 py-2 text-right font-semibold">{s.composite_score.toFixed(3)}</td>
                        <td className="px-3 py-2 text-right">{s.momentum_20d.toFixed(1)}%</td>
                        <td className="px-3 py-2 text-right">{s.volume_ratio.toFixed(2)}x</td>
                        <td className="px-3 py-2 text-right">{s.ma5.toFixed(2)}</td>
                        <td className="px-3 py-2 text-right">{s.ma20.toFixed(2)}</td>
                        <td className="px-3 py-2 text-right">{s.ma60.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Backtest Panel */}
      {activeTab === "backtest" && (
        <div className="space-y-4">
          <div className="rounded-lg border bg-card shadow-sm">
            <div className="p-4 flex flex-wrap items-end gap-4">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">开始日期</label>
                <input
                  type="date"
                  value={startDate}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setStartDate(e.target.value)}
                  className="border rounded-md px-3 py-1.5 text-sm bg-background"
                />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">结束日期</label>
                <input
                  type="date"
                  value={endDate}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setEndDate(e.target.value)}
                  className="border rounded-md px-3 py-1.5 text-sm bg-background"
                />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">初始资金</label>
                <input
                  type="number"
                  value={initialCash}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setInitialCash(Number(e.target.value))}
                  className="border rounded-md px-3 py-1.5 text-sm w-40 bg-background"
                />
              </div>
              <button
                onClick={runBacktest}
                disabled={loading}
                className="bg-primary text-primary-foreground px-4 py-1.5 rounded-md text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
              >
                {loading ? "运行中..." : "运行回测"}
              </button>
            </div>

            {backtestResult && (
              <div className="border-t p-4 space-y-6">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <MetricTile
                    icon={DollarSign}
                    label="总收益"
                    value={`${backtestResult.total_return_pct}%`}
                    tone={backtestResult.total_return_pct >= 0 ? "text-green-600" : "text-red-600"}
                  />
                  <MetricTile
                    icon={BarChart3}
                    label="夏普比率"
                    value={backtestResult.sharpe_ratio}
                  />
                  <MetricTile
                    icon={Percent}
                    label="最大回撤"
                    value={`${backtestResult.max_drawdown_pct}%`}
                    tone="text-red-600"
                  />
                  <MetricTile
                    icon={Target}
                    label="胜率"
                    value={`${backtestResult.win_rate}%`}
                  />
                  <MetricTile
                    icon={TrendingUp}
                    label="年化收益"
                    value={`${backtestResult.annualized_return_pct}%`}
                  />
                  <MetricTile
                    icon={Zap}
                    label="盈亏比"
                    value={backtestResult.profit_factor}
                  />
                  <MetricTile
                    icon={Clock}
                    label="交易次数"
                    value={backtestResult.num_trades}
                  />
                  <MetricTile
                    icon={Activity}
                    label="平均持仓"
                    value={`${backtestResult.avg_holding_days}天`}
                  />
                </div>

                {backtestResult.equity_curve && backtestResult.equity_curve.length > 0 && (
                  <div>
                    <h3 className="text-sm font-medium mb-2">收益曲线</h3>
                    <div ref={chartRef} className="w-full h-80 rounded-md border bg-background" />
                  </div>
                )}

                {sellTrades.length > 0 && (
                  <div>
                    <h3 className="text-sm font-medium mb-2">已平仓交易</h3>
                    <div className="overflow-x-auto rounded-md border">
                      <table className="w-full text-sm">
                        <thead className="bg-muted/50">
                          <tr>
                            <th className="px-3 py-2 text-left font-medium">日期</th>
                            <th className="px-3 py-2 text-left font-medium">代码</th>
                            <th className="px-3 py-2 text-right font-medium">卖出价</th>
                            <th className="px-3 py-2 text-right font-medium">数量</th>
                            <th className="px-3 py-2 text-right font-medium">盈亏%</th>
                            <th className="px-3 py-2 text-right font-medium">天数</th>
                            <th className="px-3 py-2 text-left font-medium">原因</th>
                          </tr>
                        </thead>
                        <tbody>
                          {sellTrades.map((t, idx) => (
                            <tr key={`${t.symbol}-${t.date}-${idx}`} className="border-b last:border-b-0 hover:bg-muted/30">
                              <td className="px-3 py-2">{t.date}</td>
                              <td className="px-3 py-2 font-mono">{t.symbol}</td>
                              <td className="px-3 py-2 text-right">{t.price.toFixed(2)}</td>
                              <td className="px-3 py-2 text-right">{t.quantity}</td>
                              <td className={cn("px-3 py-2 text-right font-medium", (t.pnl_pct ?? 0) >= 0 ? "text-green-600" : "text-red-600")}>
                                {(t.pnl_pct ?? 0).toFixed(2)}%
                              </td>
                              <td className="px-3 py-2 text-right">{t.days_held ?? "—"}</td>
                              <td className="px-3 py-2 text-xs text-muted-foreground">{t.reason}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {buyTrades.length > 0 && (
                  <details className="text-sm">
                    <summary className="cursor-pointer font-medium text-muted-foreground hover:text-foreground">
                      查看 {buyTrades.length} 笔买入记录
                    </summary>
                    <div className="mt-2 overflow-x-auto rounded-md border">
                      <table className="w-full text-sm">
                        <thead className="bg-muted/50">
                          <tr>
                            <th className="px-3 py-2 text-left font-medium">日期</th>
                            <th className="px-3 py-2 text-left font-medium">代码</th>
                            <th className="px-3 py-2 text-right font-medium">买入价</th>
                            <th className="px-3 py-2 text-right font-medium">数量</th>
                            <th className="px-3 py-2 text-left font-medium">原因</th>
                          </tr>
                        </thead>
                        <tbody>
                          {buyTrades.map((t, idx) => (
                            <tr key={`${t.symbol}-${t.date}-${idx}`} className="border-b last:border-b-0 hover:bg-muted/30">
                              <td className="px-3 py-2">{t.date}</td>
                              <td className="px-3 py-2 font-mono">{t.symbol}</td>
                              <td className="px-3 py-2 text-right">{t.price.toFixed(2)}</td>
                              <td className="px-3 py-2 text-right">{t.quantity}</td>
                              <td className="px-3 py-2 text-xs text-muted-foreground">{t.reason}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Profile Panel */}
      {activeTab === "profile" && (
        <div className="rounded-lg border bg-card shadow-sm">
          <div className="p-4 flex flex-wrap items-end gap-4">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">股票代码</label>
              <input
                type="text"
                value={profileSymbol}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setProfileSymbol(e.target.value)}
                className="border rounded-md px-3 py-1.5 text-sm w-40 bg-background"
              />
            </div>
            <button
              onClick={runProfile}
              disabled={loading}
              className="bg-primary text-primary-foreground px-4 py-1.5 rounded-md text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
            >
              {loading ? "分析中..." : "分析"}
            </button>
          </div>

          {profileResult?.profile && (
            <div className="border-t p-4 grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="rounded-md border p-4">
                <h3 className="font-semibold mb-3 text-sm">画像特征</h3>
                <div className="grid grid-cols-2 gap-y-2 text-sm">
                  <div className="text-muted-foreground">股性</div>
                  <div className="font-medium capitalize">{profileResult.profile.personality}</div>
                  <div className="text-muted-foreground">风险等级</div>
                  <div className="font-medium capitalize">{profileResult.profile.risk_level}</div>
                  <div className="text-muted-foreground">趋势</div>
                  <div className="font-medium capitalize">{profileResult.profile.trend_direction}</div>
                  <div className="text-muted-foreground">HV20</div>
                  <div>{profileResult.profile.hv_20}%</div>
                  <div className="text-muted-foreground">ATR%</div>
                  <div>{profileResult.profile.atr_pct}%</div>
                  <div className="text-muted-foreground">ADX</div>
                  <div>{profileResult.profile.adx_14}</div>
                  <div className="text-muted-foreground">赫斯特指数</div>
                  <div>{profileResult.profile.hurst_exponent}</div>
                </div>
              </div>
              <div className="rounded-md border p-4">
                <h3 className="font-semibold mb-3 text-sm">自适应参数</h3>
                <div className="grid grid-cols-2 gap-y-2 text-sm">
                  <div className="text-muted-foreground">止损</div>
                  <div>{(profileResult.adaptive_params.stop_loss_pct * 100).toFixed(1)}%</div>
                  <div className="text-muted-foreground">止盈</div>
                  <div>{(profileResult.adaptive_params.take_profit_pct * 100).toFixed(1)}%</div>
                  <div className="text-muted-foreground">最大仓位</div>
                  <div>{(profileResult.adaptive_params.max_position_pct * 100).toFixed(0)}%</div>
                  <div className="text-muted-foreground">单笔风险</div>
                  <div>{(profileResult.adaptive_params.risk_per_trade_pct * 100).toFixed(1)}%</div>
                  <div className="text-muted-foreground">最长持有</div>
                  <div>{profileResult.adaptive_params.max_holding_days} 天</div>
                  <div className="text-muted-foreground">移动止损</div>
                  <div>{profileResult.adaptive_params.use_trailing_stop ? `${(profileResult.adaptive_params.trailing_stop_pct * 100).toFixed(1)}%` : "关闭"}</div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="p-4 bg-red-50 text-red-700 rounded-md border border-red-200">{error}</div>
      )}
    </div>
  );
}
