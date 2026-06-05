import { useState, useEffect } from "react";
import { api, type LimitUpRecord, type Portfolio, type Trade } from "@/lib/api";
import { TrendingUp, Wallet, Newspaper, RefreshCw } from "lucide-react";

export function ASharePage() {
  const [activeTab, setActiveTab] = useState("limit-up");

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">A股</h1>
        <p className="text-sm text-muted-foreground">Ruo.ai 功能迁移</p>
      </div>

      <div className="border-b">
        <div className="flex gap-4">
          {[
            { id: "limit-up", label: "涨停梯队", icon: TrendingUp },
            { id: "portfolio", label: "模拟持仓", icon: Wallet },
            { id: "reports", label: "市场报告", icon: Newspaper },
          ].map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`flex items-center gap-1.5 pb-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === id
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon className="h-4 w-4" />
              {label}
            </button>
          ))}
        </div>
      </div>

      {activeTab === "limit-up" && <LimitUpTab />}
      {activeTab === "portfolio" && <PortfolioTab />}
      {activeTab === "reports" && <ReportsTab />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 涨停梯队
// ---------------------------------------------------------------------------

function LimitUpTab() {
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [records, setRecords] = useState<LimitUpRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const data = await api.listLimitUp(date);
      setRecords(data);
    } catch {
      setRecords([]);
    } finally {
      setLoading(false);
    }
  };

  const sync = async () => {
    setSyncing(true);
    try {
      await api.syncLimitUp(date);
      await load();
    } finally {
      setSyncing(false);
    }
  };

  useEffect(() => { load(); }, [date]);

  return (
    <div className="border rounded-lg bg-card">
      <div className="flex items-center justify-between p-4 border-b">
        <h2 className="font-semibold">涨停梯队 — {date}</h2>
        <div className="flex items-center gap-2">
          <input
            type="date"
            value={date}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setDate(e.target.value)}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
          />
          <button
            onClick={load}
            disabled={loading}
            className="inline-flex items-center justify-center h-9 px-3 rounded-md border text-sm font-medium hover:bg-muted transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </button>
          <button
            onClick={sync}
            disabled={syncing}
            className="inline-flex items-center justify-center h-9 px-4 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            {syncing ? "同步中…" : "同步数据"}
          </button>
        </div>
      </div>
      <div className="p-4">
        {records.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无数据，点击"同步数据"获取。</p>
        ) : (
          <div className="overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-muted-foreground">
                  <th className="text-left py-2 px-2">代码</th>
                  <th className="text-left py-2 px-2">名称</th>
                  <th className="text-right py-2 px-2">连板</th>
                  <th className="text-right py-2 px-2">涨停价</th>
                  <th className="text-right py-2 px-2">涨幅</th>
                  <th className="text-right py-2 px-2">封单金额</th>
                  <th className="text-left py-2 px-2">概念</th>
                  <th className="text-center py-2 px-2">状态</th>
                </tr>
              </thead>
              <tbody>
                {records.map((r) => (
                  <tr key={r.symbol} className="border-b hover:bg-muted/50">
                    <td className="py-2 px-2 font-mono">{r.symbol}</td>
                    <td className="py-2 px-2">{r.name}</td>
                    <td className="py-2 px-2 text-right font-bold">{r.limit_up_count}</td>
                    <td className="py-2 px-2 text-right">{r.limit_up_price.toFixed(2)}</td>
                    <td className="py-2 px-2 text-right">{(r.change_pct * 100).toFixed(2)}%</td>
                    <td className="py-2 px-2 text-right">{(r.seal_amount / 1e4).toFixed(0)}万</td>
                    <td className="py-2 px-2 text-xs text-muted-foreground max-w-[200px] truncate">{r.concept}</td>
                    <td className="py-2 px-2 text-center">
                      {r.is_sealed ? (
                        <span className="text-xs bg-green-100 text-green-700 px-1.5 py-0.5 rounded">封板</span>
                      ) : (
                        <span className="text-xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded">炸板</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 模拟持仓
// ---------------------------------------------------------------------------

function PortfolioTab() {
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
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <button
          onClick={loadPortfolios}
          disabled={loading}
          className="inline-flex items-center justify-center h-9 px-3 rounded-md border text-sm font-medium hover:bg-muted transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {portfolios.length === 0 ? (
        <div className="border rounded-lg bg-card">
          <div className="py-8 text-center text-sm text-muted-foreground">
            暂无模拟账户，请通过 API 创建。
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="border rounded-lg bg-card lg:col-span-1">
            <div className="p-4 border-b">
              <h2 className="font-semibold text-base">账户列表</h2>
            </div>
            <div className="p-4 space-y-2">
              {portfolios.map((p) => (
                <button
                  key={p.portfolio_id}
                  onClick={() => setSelectedId(p.portfolio_id)}
                  className={`w-full text-left p-3 rounded-lg border transition-colors ${
                    p.portfolio_id === selectedId
                      ? "border-primary bg-primary/5"
                      : "border-border hover:bg-muted/50"
                  }`}
                >
                  <div className="font-medium">{p.name}</div>
                  <div className="text-xs text-muted-foreground mt-1">
                    总资产: ¥{p.total_value.toLocaleString()} | 收益: {p.total_return_pct}%
                  </div>
                </button>
              ))}
            </div>
          </div>

          <div className="border rounded-lg bg-card lg:col-span-2">
            <div className="p-4 border-b">
              <h2 className="font-semibold text-base">{selected?.name} — 持仓明细</h2>
            </div>
            <div className="p-4">
              {selected && (
                <div className="grid grid-cols-3 gap-4 mb-4">
                  <div className="text-center p-3 bg-muted rounded-lg">
                    <div className="text-xs text-muted-foreground">总资产</div>
                    <div className="text-lg font-bold">¥{selected.total_value.toLocaleString()}</div>
                  </div>
                  <div className="text-center p-3 bg-muted rounded-lg">
                    <div className="text-xs text-muted-foreground">可用现金</div>
                    <div className="text-lg font-bold">¥{selected.cash.toLocaleString()}</div>
                  </div>
                  <div className="text-center p-3 bg-muted rounded-lg">
                    <div className="text-xs text-muted-foreground">累计盈亏</div>
                    <div className={`text-lg font-bold ${selected.total_pnl >= 0 ? "text-green-600" : "text-red-600"}`}>
                      ¥{selected.total_pnl.toLocaleString()}
                    </div>
                  </div>
                </div>
              )}

              {trades.length === 0 ? (
                <p className="text-sm text-muted-foreground">暂无交易记录。</p>
              ) : (
                <div className="overflow-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-muted-foreground">
                        <th className="text-left py-2 px-2">代码</th>
                        <th className="text-left py-2 px-2">方向</th>
                        <th className="text-right py-2 px-2">数量</th>
                        <th className="text-right py-2 px-2">价格</th>
                        <th className="text-right py-2 px-2">金额</th>
                        <th className="text-right py-2 px-2">盈亏</th>
                        <th className="text-center py-2 px-2">状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      {trades.map((t) => (
                        <tr key={t.trade_id} className="border-b hover:bg-muted/50">
                          <td className="py-2 px-2 font-mono">{t.symbol}</td>
                          <td className="py-2 px-2">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${
                              t.side === "buy" ? "bg-blue-100 text-blue-700" : "bg-orange-100 text-orange-700"
                            }`}>
                              {t.side === "buy" ? "买入" : "卖出"}
                            </span>
                          </td>
                          <td className="py-2 px-2 text-right">{t.quantity}</td>
                          <td className="py-2 px-2 text-right">{t.price.toFixed(2)}</td>
                          <td className="py-2 px-2 text-right">¥{t.amount.toLocaleString()}</td>
                          <td className={`py-2 px-2 text-right ${t.pnl >= 0 ? "text-green-600" : "text-red-600"}`}>
                            {t.pnl !== 0 ? `¥${t.pnl.toLocaleString()}` : "—"}
                          </td>
                          <td className="py-2 px-2 text-center">
                            <span className="text-xs text-muted-foreground">{t.status}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 市场报告
// ---------------------------------------------------------------------------

function ReportsTab() {
  const [kind, setKind] = useState("close");
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [markdown, setMarkdown] = useState("");
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const data = await api.getReport(kind, date);
      setMarkdown(data.markdown);
    } catch {
      setMarkdown("");
    } finally {
      setLoading(false);
    }
  };

  const generate = async () => {
    setLoading(true);
    try {
      await api.generateReport(kind, date);
      await load();
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [kind, date]);

  const kindLabels: Record<string, string> = {
    open: "开盘报告",
    close: "收盘复盘",
    weekly: "周度复盘",
  };

  return (
    <div className="border rounded-lg bg-card">
      <div className="flex items-center justify-between p-4 border-b">
        <h2 className="font-semibold">{kindLabels[kind]} — {date}</h2>
        <div className="flex items-center gap-2">
          <select
            value={kind}
            onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setKind(e.target.value)}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
          >
            <option value="open">开盘报告</option>
            <option value="close">收盘复盘</option>
            <option value="weekly">周度复盘</option>
          </select>
          <input
            type="date"
            value={date}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setDate(e.target.value)}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
          />
          <button
            onClick={load}
            disabled={loading}
            className="inline-flex items-center justify-center h-9 px-3 rounded-md border text-sm font-medium hover:bg-muted transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </button>
          <button
            onClick={generate}
            disabled={loading}
            className="inline-flex items-center justify-center h-9 px-4 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            {loading ? "生成中…" : "生成报告"}
          </button>
        </div>
      </div>
      <div className="p-4">
        {markdown ? (
          <div className="prose prose-sm max-w-none dark:prose-invert">
            <pre className="whitespace-pre-wrap font-sans text-sm">{markdown}</pre>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">暂无报告，点击"生成报告"创建。</p>
        )}
      </div>
    </div>
  );
}
