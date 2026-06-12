import { useEffect, useState } from "react";
import { Loader2, RefreshCw, TrendingDown, TrendingUp, Wallet } from "lucide-react";
import { api, type Portfolio, type Trade } from "@/lib/api";
import { cn } from "@/lib/utils";
import { SectionHeader } from "@/components/common/SectionHeader";
import { MetricTile } from "@/components/common/MetricTile";

export function PortfolioPage() {
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(false);

  const loadPortfolios = async () => {
    setLoading(true);
    try {
      const data = await api.listPortfolios();
      setPortfolios(data);
      if (data.length > 0 && !selectedId) setSelectedId(data[0].portfolio_id);
    } catch {
      setPortfolios([]);
    } finally {
      setLoading(false);
    }
  };

  const loadTrades = async (id: string) => {
    if (!id) return;
    try {
      const data = await api.listTrades(id);
      setTrades(data);
    } catch {
      setTrades([]);
    }
  };

  useEffect(() => { loadPortfolios(); }, []);
  useEffect(() => { loadTrades(selectedId); }, [selectedId]);

  const selected = portfolios.find((p) => p.portfolio_id === selectedId);

  return (
    <div className="min-h-full bg-background">
      <div className="mx-auto max-w-[1500px] p-3 md:p-5">
        <div className="mb-4 overflow-hidden rounded-lg border bg-card">
          <div className="border-b bg-muted/25 px-4 py-3">
            <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="inline-flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary">
                    <Wallet className="h-4 w-4" />
                  </span>
                  <h1 className="text-xl font-semibold tracking-normal">模拟持仓</h1>
                  {selected && (
                    <span className="rounded border bg-background px-2 py-1 text-xs text-muted-foreground">
                      {selected.name}
                    </span>
                  )}
                </div>
                <p className="mt-1 truncate text-sm text-muted-foreground">
                  跟踪 A 股模拟账户的资金、持仓与交易明细
                </p>
              </div>
              <button
                onClick={loadPortfolios}
                disabled={loading}
                className="inline-flex h-9 items-center gap-2 rounded-md border bg-background px-3 text-sm hover:bg-muted disabled:opacity-50"
              >
                <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
                刷新
              </button>
            </div>
          </div>

          {selected && (
            <div className="grid grid-cols-2 divide-x divide-y md:grid-cols-4 md:divide-y-0">
              <MetricTile
                icon={Wallet}
                label="总资产"
                value={`¥${selected.total_value.toLocaleString()}`}
                tone="text-sky-600"
              />
              <MetricTile
                icon={Wallet}
                label="可用现金"
                value={`¥${selected.cash.toLocaleString()}`}
                tone="text-muted-foreground"
              />
              <MetricTile
                icon={selected.total_pnl >= 0 ? TrendingUp : TrendingDown}
                label="累计盈亏"
                value={`${selected.total_pnl >= 0 ? "+" : ""}¥${selected.total_pnl.toLocaleString()}`}
                tone={selected.total_pnl >= 0 ? "text-emerald-600" : "text-red-600"}
              />
              <MetricTile
                icon={TrendingUp}
                label="累计收益率"
                value={`${selected.total_return_pct >= 0 ? "+" : ""}${selected.total_return_pct}%`}
                tone={selected.total_return_pct >= 0 ? "text-emerald-600" : "text-red-600"}
              />
            </div>
          )}
        </div>

        {portfolios.length === 0 ? (
          <div className="rounded-lg border border-dashed bg-card p-12 text-center">
            <Wallet className="mx-auto mb-2 h-6 w-6 text-muted-foreground/60" />
            <p className="text-sm text-muted-foreground">暂无模拟账户</p>
            <p className="mt-1 text-xs text-muted-foreground/70">通过 API 创建模拟账户后再来查看</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
            <aside className="rounded-lg border bg-card">
              <SectionHeader icon={Wallet} title="账户列表" meta={`${portfolios.length} 个`} />
              <div className="max-h-[640px] overflow-auto p-2">
                {portfolios.map((p) => {
                  const active = p.portfolio_id === selectedId;
                  return (
                    <button
                      key={p.portfolio_id}
                      onClick={() => setSelectedId(p.portfolio_id)}
                      className={cn(
                        "mb-2 w-full rounded-md border bg-background p-3 text-left transition-colors",
                        active
                          ? "border-primary shadow-sm ring-1 ring-primary/20"
                          : "hover:border-primary/40 hover:bg-muted/40",
                      )}
                    >
                      <div className="flex items-start gap-2">
                        <span
                          className={cn(
                            "mt-1 h-2 w-2 shrink-0 rounded-full",
                            active ? "bg-primary" : "bg-border",
                          )}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-medium">{p.name}</div>
                          <div className="mt-1 text-xs text-muted-foreground">
                            总资产 ¥{p.total_value.toLocaleString()}
                          </div>
                          <div className="mt-0.5 text-xs">
                            <span
                              className={cn(
                                "font-medium",
                                p.total_pnl >= 0 ? "text-emerald-600" : "text-red-600",
                              )}
                            >
                              {p.total_pnl >= 0 ? "+" : ""}{p.total_return_pct}%
                            </span>
                            <span className="ml-1 text-muted-foreground">累计收益</span>
                          </div>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </aside>

            <main className="rounded-lg border bg-card">
              <SectionHeader icon={TrendingUp} title="交易明细" meta={selected ? selected.name : "未选择账户"} />
              <div className="overflow-x-auto p-2">
                {trades.length === 0 ? (
                  <div className="py-12 text-center text-sm text-muted-foreground">
                    {loading ? <Loader2 className="mx-auto h-5 w-5 animate-spin" /> : "暂无交易记录"}
                  </div>
                ) : (
                  <table className="w-full min-w-[680px] text-sm">
                    <thead className="border-b bg-muted/40 text-xs text-muted-foreground">
                      <tr>
                        <th className="px-3 py-2 text-left font-medium">代码</th>
                        <th className="px-3 py-2 text-left font-medium">方向</th>
                        <th className="px-3 py-2 text-right font-medium">数量</th>
                        <th className="px-3 py-2 text-right font-medium">价格</th>
                        <th className="px-3 py-2 text-right font-medium">金额</th>
                        <th className="px-3 py-2 text-right font-medium">盈亏</th>
                        <th className="px-3 py-2 text-center font-medium">状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      {trades.map((t) => (
                        <tr key={t.trade_id} className="border-b last:border-0 hover:bg-muted/30">
                          <td className="px-3 py-2.5 font-mono text-xs">{t.symbol}</td>
                          <td className="px-3 py-2.5">
                            <span
                              className={cn(
                                "rounded px-2 py-0.5 text-[11px] font-medium",
                                t.side === "buy"
                                  ? "bg-sky-500/10 text-sky-600"
                                  : "bg-orange-500/10 text-orange-600",
                              )}
                            >
                              {t.side === "buy" ? "买入" : "卖出"}
                            </span>
                          </td>
                          <td className="px-3 py-2.5 text-right tabular-nums">{t.quantity}</td>
                          <td className="px-3 py-2.5 text-right tabular-nums">{t.price.toFixed(2)}</td>
                          <td className="px-3 py-2.5 text-right tabular-nums">¥{t.amount.toLocaleString()}</td>
                          <td
                            className={cn(
                              "px-3 py-2.5 text-right tabular-nums",
                              t.pnl > 0 && "font-semibold text-emerald-600",
                              t.pnl < 0 && "font-semibold text-red-600",
                            )}
                          >
                            {t.pnl !== 0 ? `${t.pnl >= 0 ? "+" : ""}¥${t.pnl.toLocaleString()}` : "—"}
                          </td>
                          <td className="px-3 py-2.5 text-center text-xs text-muted-foreground">{t.status}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </main>
          </div>
        )}
      </div>
    </div>
  );
}
