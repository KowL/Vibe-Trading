import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Flame,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trophy,
} from "lucide-react";
import { api, type LimitUpRecord } from "@/lib/api";
import { cn } from "@/lib/utils";
import { SectionHeader } from "@/components/common/SectionHeader";
import { MetricTile } from "@/components/common/MetricTile";

interface TierSpec {
  key: string;
  label: string;
  tone: string;
  chip: string;
  description: string;
  match: (count: number) => boolean;
}

// 高位在前：7板及以上 → 6 → 5 → 4 → 3 → 2 → 首板 → 炸板
const TIER_ORDER: TierSpec[] = [
  { key: "7+", label: "7板及以上", tone: "text-red-700", chip: "bg-red-500/15 text-red-700 border-red-500/40", description: "高标龙头，市场核心", match: c => c >= 7 },
  { key: "6", label: "6板", tone: "text-red-600", chip: "bg-red-500/10 text-red-600 border-red-500/30", description: "六连板，妖股候选", match: c => c === 6 },
  { key: "5", label: "5板", tone: "text-red-600", chip: "bg-red-500/10 text-red-600 border-red-500/30", description: "五连板，高位启动", match: c => c === 5 },
  { key: "4", label: "4板", tone: "text-orange-600", chip: "bg-orange-500/10 text-orange-600 border-orange-500/30", description: "四连板，关注分歧", match: c => c === 4 },
  { key: "3", label: "3板", tone: "text-orange-600", chip: "bg-orange-500/10 text-orange-600 border-orange-500/30", description: "三连板，情绪升温", match: c => c === 3 },
  { key: "2", label: "2板", tone: "text-emerald-600", chip: "bg-emerald-500/10 text-emerald-600 border-emerald-500/30", description: "二连板，主力发酵", match: c => c === 2 },
  { key: "1", label: "首板", tone: "text-emerald-600", chip: "bg-emerald-500/10 text-emerald-600 border-emerald-500/30", description: "首次涨停，今日启动信号", match: c => c === 1 },
  { key: "broken", label: "炸板", tone: "text-muted-foreground", chip: "bg-muted text-muted-foreground border-border", description: "触及涨停后未能封住，警惕回撤", match: () => true },
];

function classify(record: LimitUpRecord): string {
  if (!record.is_sealed) return "broken";
  const count = record.limit_up_count || 1;
  return TIER_ORDER.find((t) => t.key !== "broken" && t.match(count))?.key ?? "1";
}

function formatWan(amount: number | null | undefined): { value: string; tone: string; pct: number } {
  if (amount == null) return { value: "—", tone: "text-muted-foreground", pct: 0 };
  const wan = amount / 1e4;
  if (wan >= 10000) {
    return { value: `${(wan / 10000).toFixed(2)} 亿`, tone: "text-red-600 font-semibold", pct: 100 };
  }
  if (wan >= 5000) {
    return { value: `${wan.toFixed(0)} 万`, tone: "text-orange-600 font-semibold", pct: 75 };
  }
  if (wan >= 1000) {
    return { value: `${wan.toFixed(0)} 万`, tone: "text-amber-600", pct: 45 };
  }
  if (wan >= 100) {
    return { value: `${wan.toFixed(0)} 万`, tone: "text-muted-foreground", pct: 20 };
  }
  return { value: `${wan.toFixed(0)} 万`, tone: "text-muted-foreground/70", pct: 8 };
}

function formatFirstTime(t: string | null | undefined): string {
  if (!t) return "—";
  if (t.length >= 5) return t.slice(0, 5);
  return t;
}

function formatPct(value: number | null | undefined, digits = 1): string {
  if (value == null) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function LimitUpPage() {
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [records, setRecords] = useState<LimitUpRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [lastSync, setLastSync] = useState<string>("");
  const [collapsedTiers, setCollapsedTiers] = useState<Record<string, boolean>>({
    broken: true,
  });

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

  useEffect(() => {
    const es = new EventSource("/ashare/events");
    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data);
        if (event.event_type === "ashare_limit_up_sync") {
          setLastSync(`${event.trade_date} 同步完成：${event.count} 条`);
          if (event.trade_date === date) load();
        }
      } catch {
        // ignore parse errors
      }
    };
    return () => es.close();
  }, [date]);

  useEffect(() => { load(); }, [date]);

  const stats = useMemo(() => {
    const sealed = records.filter((r) => r.is_sealed);
    const maxStreak = records.reduce((m, r) => Math.max(m, r.limit_up_count), 0);
    return {
      total: records.length,
      sealedCount: sealed.length,
      sealRate: records.length === 0 ? 0 : sealed.length / records.length,
      maxStreak,
      brokenCount: records.length - sealed.length,
    };
  }, [records]);

  const groups = useMemo(() => {
    const out: Record<string, LimitUpRecord[]> = {};
    for (const t of TIER_ORDER) out[t.key] = [];
    for (const r of records) {
      const key = classify(r);
      out[key].push(r);
    }
    for (const key of Object.keys(out)) {
      out[key].sort((a, b) => {
        const sealDiff = (b.seal_amount ?? 0) - (a.seal_amount ?? 0);
        const ratioA = a.turnover_amount ?? 0;
        const ratioB = b.turnover_amount ?? 0;
        return ratioB - ratioA || sealDiff;
      });
    }
    return out;
  }, [records]);

  return (
    <div className="min-h-full bg-background">
      <div className="mx-auto max-w-[1500px] p-3 md:p-5">
        <div className="mb-4 overflow-hidden rounded-lg border bg-card">
          <div className="border-b bg-muted/25 px-4 py-3">
            <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="inline-flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary">
                    <Flame className="h-4 w-4" />
                  </span>
                  <h1 className="text-xl font-semibold tracking-normal">涨停梯队</h1>
                  <span className="rounded border bg-background px-2 py-1 text-xs text-muted-foreground">
                    {date}
                  </span>
                  {lastSync && (
                    <span className="rounded border border-emerald-500/30 bg-emerald-500/10 px-2 py-1 text-xs text-emerald-600">
                      {lastSync}
                    </span>
                  )}
                </div>
                <p className="mt-1 truncate text-sm text-muted-foreground">
                  按连板高度分组展示当日涨停个股，支持同步与实时事件推送
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="date"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                />
                <button
                  onClick={load}
                  disabled={loading}
                  className="inline-flex h-9 items-center gap-2 rounded-md border bg-background px-3 text-sm hover:bg-muted disabled:opacity-50"
                >
                  <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
                  刷新
                </button>
                <button
                  onClick={sync}
                  disabled={syncing}
                  className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-3 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  {syncing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                  {syncing ? "同步中…" : "同步数据"}
                </button>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 divide-x divide-y md:grid-cols-4 md:divide-y-0">
            <MetricTile
              icon={Flame}
              label="涨停家数"
              value={stats.total}
              hint={loading ? "加载中…" : `${date} 当日`}
              tone="text-red-600"
            />
            <MetricTile
              icon={ShieldCheck}
              label="封板率"
              value={records.length === 0 ? "--" : `${(stats.sealRate * 100).toFixed(0)}%`}
              hint={stats.sealedCount > 0 ? `封板 ${stats.sealedCount} / ${stats.total}` : "暂无"}
              tone="text-emerald-600"
            />
            <MetricTile
              icon={Trophy}
              label="最高连板"
              value={stats.maxStreak > 0 ? `${stats.maxStreak} 板` : "--"}
              hint={stats.maxStreak >= 5 ? "高标梯队活跃" : "暂无龙头"}
              tone="text-amber-600"
            />
            <MetricTile
              icon={AlertTriangle}
              label="炸板数"
              value={stats.brokenCount}
              hint={stats.brokenCount > 0 ? "警惕回撤" : "全员封板"}
              tone="text-muted-foreground"
            />
          </div>
        </div>

        {loading && records.length === 0 ? (
          <div className="rounded-lg border bg-card p-12 text-center text-sm text-muted-foreground">
            <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin" />
            加载中…
          </div>
        ) : records.length === 0 ? (
          <div className="rounded-lg border border-dashed bg-card p-12 text-center">
            <Sparkles className="mx-auto mb-2 h-6 w-6 text-muted-foreground/60" />
            <p className="text-sm text-muted-foreground">该日期暂无涨停数据</p>
            <p className="mt-1 text-xs text-muted-foreground/70">点击「同步数据」从 AmazingData 拉取</p>
          </div>
        ) : (
          TIER_ORDER.map((tier) => {
            const list = groups[tier.key];
            if (!list || list.length === 0) return null;
            const collapsed = collapsedTiers[tier.key] ?? false;
            return (
              <section key={tier.key} className="mb-4 overflow-hidden rounded-lg border bg-card">
                <SectionHeader
                  icon={collapsed ? ChevronRight : ChevronDown}
                  title={tier.label}
                  meta={`${list.length} 只 · ${tier.description}`}
                  onClick={() =>
                    setCollapsedTiers((prev) => ({ ...prev, [tier.key]: !prev[tier.key] }))
                  }
                  trailing={
                    <span className={cn("rounded border px-2 py-0.5 text-[11px] font-medium", tier.chip)}>
                      {list.length}
                    </span>
                  }
                />
                {!collapsed && (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[820px] text-sm">
                      <thead className="border-b bg-muted/40 text-xs text-muted-foreground">
                        <tr>
                          <th className="px-3 py-2 text-left font-medium">代码</th>
                          <th className="px-3 py-2 text-left font-medium">名称</th>
                          <th className="px-3 py-2 text-right font-medium">涨停价</th>
                          <th className="px-3 py-2 text-right font-medium">涨幅</th>
                          <th className="px-3 py-2 text-left font-medium">封单金额</th>
                          <th className="px-3 py-2 text-right font-medium">换手率</th>
                          <th className="px-3 py-2 text-right font-medium">首次封板</th>
                          <th className="px-3 py-2 text-left font-medium">概念</th>
                          <th className="px-3 py-2 text-center font-medium">状态</th>
                        </tr>
                      </thead>
                      <tbody>
                        {list.map((r) => {
                          const seal = formatWan(r.seal_amount);
                          const hot = r.is_sealed && r.limit_up_count >= 7;
                          return (
                            <tr
                              key={r.symbol}
                              className={cn(
                                "border-b last:border-0 transition-colors hover:bg-muted/30",
                                !r.is_sealed && "opacity-75",
                              )}
                            >
                              <td className="px-3 py-2.5 font-mono text-xs">
                                <span className="inline-flex items-center gap-1">
                                  {hot && <Flame className="h-3 w-3 text-red-600" />}
                                  {r.symbol}
                                </span>
                              </td>
                              <td className="px-3 py-2.5">
                                <div className="font-medium">{r.name}</div>
                                {r.industry && (
                                  <div className="mt-0.5 text-[11px] text-muted-foreground">{r.industry}</div>
                                )}
                              </td>
                              <td className="px-3 py-2.5 text-right tabular-nums">
                                {r.limit_up_price != null ? r.limit_up_price.toFixed(2) : "—"}
                              </td>
                              <td className="px-3 py-2.5 text-right">
                                <span className="font-semibold text-red-600">+{formatPct(r.change_pct, 2)}</span>
                              </td>
                              <td className="px-3 py-2.5">
                                <div className="flex items-center gap-2">
                                  <div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
                                    <div
                                      className={cn(
                                        "h-full",
                                        seal.pct >= 75 ? "bg-red-500" : seal.pct >= 45 ? "bg-orange-500" : seal.pct >= 20 ? "bg-amber-500" : "bg-muted-foreground/40",
                                      )}
                                      style={{ width: `${seal.pct}%` }}
                                    />
                                  </div>
                                  <span className={cn("text-xs tabular-nums", seal.tone)}>{seal.value}</span>
                                </div>
                              </td>
                              <td className="px-3 py-2.5 text-right tabular-nums text-muted-foreground">
                                {formatPct(r.turnover_ratio, 1)}
                              </td>
                              <td className="px-3 py-2.5 text-right tabular-nums">{formatFirstTime(r.first_time)}</td>
                              <td className="max-w-[200px] truncate px-3 py-2.5 text-xs text-muted-foreground" title={r.concept ?? ""}>
                                {r.concept || "—"}
                              </td>
                              <td className="px-3 py-2.5 text-center">
                                {r.is_sealed ? (
                                  <span className="rounded bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-600">
                                    封板
                                  </span>
                                ) : (
                                  <span className="rounded bg-red-500/10 px-2 py-0.5 text-[11px] font-medium text-red-600">
                                    炸板
                                  </span>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            );
          })
        )}
      </div>
    </div>
  );
}
