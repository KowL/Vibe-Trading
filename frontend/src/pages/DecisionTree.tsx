import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  BrainCircuit,
  CheckCircle2,
  CircleDot,
  Eye,
  GitBranch,
  ListChecks,
  Network,
  Plus,
  PlayCircle,
  RefreshCw,
  Save,
  Search,
  SlidersHorizontal,
  Target,
  ToggleLeft,
  ToggleRight,
  Trash2,
  X,
} from "lucide-react";
import { request } from "@/lib/api";
import { cn } from "@/lib/utils";
import { SectionHeader } from "@/components/common/SectionHeader";
import { MetricTile } from "@/components/common/MetricTile";
import { MiniStat } from "@/components/common/MiniStat";

type ActionType = "买入" | "卖出" | "持有" | "观望" | "减仓" | "加仓" | "止损" | "止盈";

interface RuleCondition {
  field: string;
  operator: string;
  value: unknown;
  description?: string;
}

interface DecisionRule {
  id: string;
  name: string;
  description?: string;
  conditions: RuleCondition[];
  action: ActionType;
  position_pct?: number | null;
  priority: number;
  enabled: boolean;
  created_by?: string;
  source?: string;
  version: number;
}

interface DecisionTreeNode {
  id: string;
  type: string;
  label: string;
  description?: string;
  rule_id?: string;
  enabled?: boolean;
  priority?: number;
  position?: { x: number; y: number };
  style?: { color: string; shape: string; size: number };
}

interface DecisionTreeEdge {
  source: string;
  target: string;
  label?: string;
}

interface DecisionTreeViz {
  nodes: DecisionTreeNode[];
  edges: DecisionTreeEdge[];
  layout: string;
}

interface DecisionTreeItem {
  id: string;
  name: string;
  description?: string;
  version: number;
  active: boolean;
  rule_count: number;
  created_at: string;
  updated_at?: string;
}

interface DecisionTreeDetail extends DecisionTreeItem {
  rules: DecisionRule[];
}

interface SentimentResult {
  sentiment_cycle: string;
  details: Record<string, number>;
  description: string;
}

interface EvaluateResult {
  matched: boolean;
  rule?: DecisionRule;
  context: Record<string, unknown>;
  recommendation: {
    action: ActionType;
    position_pct: number;
    message: string;
  };
}

interface RuleFormState {
  name: string;
  description: string;
  field: string;
  operator: string;
  value: string;
  action: ActionType;
  position_pct: string;
  priority: string;
}

const ACTIONS: ActionType[] = ["买入", "卖出", "持有", "观望", "减仓", "加仓", "止损", "止盈"];
const OPERATORS = [
  { value: "eq", label: "=" },
  { value: "neq", label: "!=" },
  { value: "gt", label: ">" },
  { value: "gte", label: ">=" },
  { value: "lt", label: "<" },
  { value: "lte", label: "<=" },
  { value: "in", label: "in" },
  { value: "between", label: "between" },
  { value: "contains", label: "contains" },
];

const DEFAULT_RULE_FORM: RuleFormState = {
  name: "",
  description: "",
  field: "sentiment_cycle",
  operator: "eq",
  value: "冰点",
  action: "买入",
  position_pct: "30",
  priority: "10",
};

const DEFAULT_CONTEXT = JSON.stringify({ sentiment_cycle: "冰点" }, null, 2);
const inputClassName = "h-10 w-full rounded-md border bg-background px-3 text-sm outline-none focus:border-primary";

export function DecisionTree() {
  const [trees, setTrees] = useState<DecisionTreeItem[]>([]);
  const [selectedTree, setSelectedTree] = useState<string | null>(null);
  const [treeDetail, setTreeDetail] = useState<DecisionTreeDetail | null>(null);
  const [visualization, setVisualization] = useState<DecisionTreeViz | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newTreeName, setNewTreeName] = useState("");
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [ruleForm, setRuleForm] = useState<RuleFormState>(DEFAULT_RULE_FORM);
  const [sentimentForm, setSentimentForm] = useState({
    limit_up_count: "35",
    limit_down_count: "8",
    max_limit_up_streak: "3",
    broken_board_rate: "0.2",
    up_down_ratio: "1.3",
    prev_limit_up_premium: "0",
  });
  const [sentimentResult, setSentimentResult] = useState<SentimentResult | null>(null);
  const [contextText, setContextText] = useState(DEFAULT_CONTEXT);
  const [evaluateResult, setEvaluateResult] = useState<EvaluateResult | null>(null);

  const selectedRules = treeDetail?.rules ?? [];
  const enabledRuleCount = useMemo(() => selectedRules.filter((rule) => rule.enabled).length, [selectedRules]);
  const disabledRuleCount = selectedRules.length - enabledRuleCount;
  const lastUpdated = treeDetail?.updated_at || treeDetail?.created_at;

  useEffect(() => {
    loadTrees();
  }, []);

  useEffect(() => {
    if (!selectedTree) {
      setTreeDetail(null);
      setVisualization(null);
      return;
    }
    loadTree(selectedTree);
  }, [selectedTree]);

  const refreshSelected = async () => {
    if (!selectedTree) return;
    await Promise.all([loadTrees(selectedTree), loadTree(selectedTree)]);
  };

  const loadTrees = async (preferredId?: string) => {
    try {
      const data = await request<DecisionTreeItem[]>("/decision-tree/list");
      setTrees(data);
      setError(null);
      const target = preferredId ?? selectedTree;
      if (data.length > 0 && (!target || !data.some((tree) => tree.id === target))) {
        setSelectedTree(data[0].id);
      }
    } catch {
      setError("加载决策树列表失败");
    }
  };

  const loadTree = async (treeId: string) => {
    setLoading(true);
    try {
      const [detail, viz] = await Promise.all([
        request<DecisionTreeDetail>(`/decision-tree/${treeId}`),
        request<DecisionTreeViz>(`/decision-tree/${treeId}/visualize`),
      ]);
      setTreeDetail(detail);
      setVisualization(viz);
      setError(null);
    } catch {
      setError("加载决策树失败");
    } finally {
      setLoading(false);
    }
  };

  const createTree = async () => {
    if (!newTreeName.trim()) return;
    setBusy(true);
    try {
      const created = await request<{ id: string }>("/decision-tree/create", {
        method: "POST",
        body: JSON.stringify({ name: newTreeName.trim() }),
      });
      setNewTreeName("");
      setShowCreateForm(false);
      setSelectedTree(created.id);
      await loadTrees(created.id);
    } catch {
      setError("创建决策树失败");
    } finally {
      setBusy(false);
    }
  };

  const deleteTree = async (treeId: string) => {
    if (treeId === "default_tree") {
      setError("默认决策树不能删除");
      return;
    }
    if (!confirm("确定删除此决策树？")) return;
    setBusy(true);
    try {
      await request<{ status: string }>(`/decision-tree/${treeId}`, { method: "DELETE" });
      setSelectedTree((current) => (current === treeId ? null : current));
      await loadTrees();
    } catch {
      setError("删除决策树失败");
    } finally {
      setBusy(false);
    }
  };

  const addRule = async () => {
    if (!selectedTree || !ruleForm.name.trim()) return;
    setBusy(true);
    try {
      const condition: RuleCondition = {
        field: ruleForm.field.trim(),
        operator: ruleForm.operator,
        value: parseConditionValue(ruleForm.value, ruleForm.operator),
      };
      await request<{ rule_id: string }>(`/decision-tree/${selectedTree}/rules`, {
        method: "POST",
        body: JSON.stringify({
          name: ruleForm.name.trim(),
          description: ruleForm.description.trim() || undefined,
          conditions: [condition],
          action: ruleForm.action,
          position_pct: ruleForm.position_pct === "" ? null : Number(ruleForm.position_pct),
          priority: Number(ruleForm.priority || 100),
          enabled: true,
        }),
      });
      setRuleForm(DEFAULT_RULE_FORM);
      await refreshSelected();
    } catch {
      setError("添加规则失败，请检查条件值");
    } finally {
      setBusy(false);
    }
  };

  const toggleRule = async (ruleId: string) => {
    if (!selectedTree) return;
    setBusy(true);
    try {
      await request(`/decision-tree/${selectedTree}/rules/${ruleId}/toggle`, { method: "PATCH" });
      await refreshSelected();
    } catch {
      setError("更新规则状态失败");
    } finally {
      setBusy(false);
    }
  };

  const deleteRule = async (ruleId: string) => {
    if (!selectedTree || !confirm("确定删除此规则？")) return;
    setBusy(true);
    try {
      await request(`/decision-tree/${selectedTree}/rules/${ruleId}`, { method: "DELETE" });
      await refreshSelected();
    } catch {
      setError("删除规则失败");
    } finally {
      setBusy(false);
    }
  };

  const analyzeSentiment = async () => {
    setBusy(true);
    try {
      const body = Object.fromEntries(
        Object.entries(sentimentForm).map(([key, value]) => [key, Number(value)]),
      );
      const result = await request<SentimentResult>("/decision-tree/sentiment", {
        method: "POST",
        body: JSON.stringify(body),
      });
      setSentimentResult(result);
      setContextText(JSON.stringify({ sentiment_cycle: result.sentiment_cycle }, null, 2));
      setError(null);
    } catch {
      setError("情绪周期分析失败");
    } finally {
      setBusy(false);
    }
  };

  const evaluateTree = async () => {
    if (!selectedTree) return;
    setBusy(true);
    try {
      const context = JSON.parse(contextText) as Record<string, unknown>;
      const result = await request<EvaluateResult>(`/decision-tree/${selectedTree}/evaluate`, {
        method: "POST",
        body: JSON.stringify({ context }),
      });
      setEvaluateResult(result);
      setError(null);
    } catch {
      setError("评估失败，请检查 JSON 上下文");
    } finally {
      setBusy(false);
    }
  };

  const evaluateWithMarketData = async () => {
    const marketData = Object.fromEntries(
      Object.entries(sentimentForm).map(([key, value]) => [key, Number(value)]),
    );
    setContextText(JSON.stringify({ market_data: marketData }, null, 2));
    if (!selectedTree) return;
    setBusy(true);
    try {
      const result = await request<EvaluateResult>(`/decision-tree/${selectedTree}/evaluate`, {
        method: "POST",
        body: JSON.stringify({ context: { market_data: marketData } }),
      });
      setEvaluateResult(result);
      setError(null);
    } catch {
      setError("评估失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-full bg-background">
      <div className="mx-auto max-w-[1500px] p-3 md:p-5">
        <div className="mb-4 overflow-hidden rounded-lg border bg-card">
          <div className="border-b bg-muted/25 px-4 py-3">
            <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="inline-flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary">
                    <GitBranch className="h-4 w-4" />
                  </span>
                  <h1 className="text-xl font-semibold tracking-normal">交易决策树</h1>
                  {treeDetail && (
                    <span className="rounded border bg-background px-2 py-1 text-xs text-muted-foreground">
                      {treeDetail.id}
                    </span>
                  )}
                </div>
                <p className="mt-1 truncate text-sm text-muted-foreground">
                  {treeDetail ? treeDetail.name : "选择一棵决策树后维护规则、模拟情绪并运行评估"}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={refreshSelected}
                  disabled={!selectedTree || loading}
                  className="inline-flex h-9 items-center gap-2 rounded-md border bg-background px-3 text-sm hover:bg-muted disabled:opacity-50"
                >
                  <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
                  刷新
                </button>
                <button
                  onClick={() => setShowCreateForm(true)}
                  className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-3 text-sm text-primary-foreground hover:bg-primary/90"
                >
                  <Plus className="h-4 w-4" />
                  新建决策树
                </button>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 divide-x divide-y md:grid-cols-4 md:divide-y-0">
            <MetricTile icon={Network} label="规则总数" value={String(selectedRules.length)} tone="text-sky-600" />
            <MetricTile icon={CheckCircle2} label="启用规则" value={String(enabledRuleCount)} tone="text-emerald-600" />
            <MetricTile icon={CircleDot} label="停用规则" value={String(disabledRuleCount)} tone="text-amber-600" />
            <MetricTile
              icon={Target}
              label="最近评估"
              value={evaluateResult ? `${evaluateResult.recommendation.action} ${evaluateResult.recommendation.position_pct}%` : "--"}
              tone={evaluateResult?.matched ? "text-emerald-600" : "text-muted-foreground"}
            />
          </div>
        </div>

        {error && (
          <div className="mb-4 flex items-center justify-between rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            <span className="flex items-center gap-2">
              <AlertCircle className="h-4 w-4" />
              {error}
            </span>
            <button onClick={() => setError(null)} className="rounded p-1 hover:bg-destructive/10" aria-label="关闭错误">
              <X className="h-4 w-4" />
            </button>
          </div>
        )}

        {showCreateForm && (
          <section className="mb-4 rounded-lg border bg-card">
            <SectionHeader icon={Plus} title="新建决策树" meta="创建后自动切换到新树" />
            <div className="flex flex-col gap-2 p-3 md:flex-row">
              <input
                value={newTreeName}
                onChange={(event) => setNewTreeName(event.target.value)}
                onKeyDown={(event) => event.key === "Enter" && createTree()}
                placeholder="例如：短线情绪纪律树"
                className={inputClassName}
              />
              <button
                onClick={createTree}
                disabled={busy}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                <Save className="h-4 w-4" />
                创建
              </button>
              <button
                onClick={() => setShowCreateForm(false)}
                className="h-10 rounded-md border px-4 text-sm hover:bg-muted"
              >
                取消
              </button>
            </div>
          </section>
        )}

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="space-y-4">
            <section className="rounded-lg border bg-card">
              <SectionHeader icon={Search} title="决策树" meta={`${trees.length} 棵`} />
              <div className="max-h-[430px] overflow-auto p-2">
                {trees.map((tree) => {
                  const active = selectedTree === tree.id;
                  return (
                    <div
                      key={tree.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => setSelectedTree(tree.id)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") setSelectedTree(tree.id);
                      }}
                      className={cn(
                        "mb-2 cursor-pointer rounded-md border bg-background p-3 text-left transition-colors",
                        active ? "border-primary shadow-sm ring-1 ring-primary/20" : "hover:border-primary/40 hover:bg-muted/40",
                      )}
                    >
                      <div className="flex items-start gap-3">
                        <span className={cn("mt-1 h-2.5 w-2.5 rounded-full", active ? "bg-primary" : "bg-border")} />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0 truncate text-sm font-medium">{tree.name}</div>
                            {tree.active && (
                              <span className="shrink-0 rounded bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-600">
                                激活
                              </span>
                            )}
                          </div>
                          <div className="mt-1 text-xs text-muted-foreground">
                            {tree.rule_count} 条规则 · v{tree.version}
                          </div>
                          {tree.description && (
                            <div className="mt-2 line-clamp-2 text-xs text-muted-foreground">{tree.description}</div>
                          )}
                          <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
                            <span>{formatShortDate(tree.updated_at || tree.created_at)}</span>
                            {tree.id !== "default_tree" && (
                              <button
                                onClick={(event) => {
                                  event.stopPropagation();
                                  deleteTree(tree.id);
                                }}
                                className="inline-flex items-center gap-1 rounded px-1.5 py-1 hover:bg-destructive/10 hover:text-destructive"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                                删除
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>

            <section className="rounded-lg border bg-card">
              <SectionHeader icon={BrainCircuit} title="情绪周期" meta={sentimentResult?.sentiment_cycle || "未分析"} />
              <div className="p-3">
                <div className="grid grid-cols-2 gap-2">
                  {Object.entries(sentimentForm).map(([key, value]) => (
                    <label key={key} className="space-y-1 text-xs text-muted-foreground">
                      {sentimentLabel(key)}
                      <input
                        value={value}
                        onChange={(event) => setSentimentForm((prev) => ({ ...prev, [key]: event.target.value }))}
                        className="h-9 w-full rounded-md border bg-background px-2 text-sm text-foreground outline-none focus:border-primary"
                      />
                    </label>
                  ))}
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <button
                    onClick={analyzeSentiment}
                    disabled={busy}
                    className="inline-flex h-9 items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm hover:bg-muted disabled:opacity-50"
                  >
                    <Activity className="h-4 w-4" />
                    分析
                  </button>
                  <button
                    onClick={evaluateWithMarketData}
                    disabled={busy || !selectedTree}
                    className="inline-flex h-9 items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                  >
                    <PlayCircle className="h-4 w-4" />
                    评估
                  </button>
                </div>
                {sentimentResult && (
                  <div className="mt-3 border-t pt-3">
                    <div className="flex items-end justify-between gap-2">
                      <div className="text-2xl font-semibold text-primary">{sentimentResult.sentiment_cycle}</div>
                      <div className="text-xs text-muted-foreground">
                        炸板率 {formatPercent(sentimentResult.details.broken_board_rate)}
                      </div>
                    </div>
                    <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
                      <MiniStat label="涨停" value={sentimentResult.details.limit_up_count} />
                      <MiniStat label="跌停" value={sentimentResult.details.limit_down_count} />
                      <MiniStat label="涨跌比" value={sentimentResult.details.up_down_ratio} />
                    </div>
                  </div>
                )}
              </div>
            </section>
          </aside>

          <main className="space-y-4">
            <section className="grid grid-cols-1 gap-4 2xl:grid-cols-[minmax(0,1.25fr)_420px]">
              <div className="rounded-lg border bg-card">
                <SectionHeader icon={Eye} title="可视化路径" meta={lastUpdated ? `更新 ${formatShortDate(lastUpdated)}` : undefined} />
                <div className="overflow-auto p-3">
                  {loading ? (
                    <div className="flex h-[460px] items-center justify-center text-sm text-muted-foreground">加载中...</div>
                  ) : visualization ? (
                    <DecisionTreeCanvas visualization={visualization} />
                  ) : (
                    <div className="flex h-[460px] items-center justify-center rounded-md border border-dashed text-sm text-muted-foreground">
                      选择决策树
                    </div>
                  )}
                </div>
              </div>

              <div className="rounded-lg border bg-card">
                <SectionHeader icon={SlidersHorizontal} title="新增规则" meta="单条件快速录入" />
                <div className="space-y-3 p-3">
                  <input
                    value={ruleForm.name}
                    onChange={(event) => setRuleForm((prev) => ({ ...prev, name: event.target.value }))}
                    placeholder="规则名称"
                    className={inputClassName}
                  />
                  <textarea
                    value={ruleForm.description}
                    onChange={(event) => setRuleForm((prev) => ({ ...prev, description: event.target.value }))}
                    placeholder="规则描述"
                    className="min-h-16 w-full resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
                  />
                  <div className="grid grid-cols-[1fr_92px_1fr] gap-2">
                    <input
                      value={ruleForm.field}
                      onChange={(event) => setRuleForm((prev) => ({ ...prev, field: event.target.value }))}
                      placeholder="字段"
                      className={inputClassName}
                    />
                    <select
                      value={ruleForm.operator}
                      onChange={(event) => setRuleForm((prev) => ({ ...prev, operator: event.target.value }))}
                      className="h-10 rounded-md border bg-background px-2 text-sm outline-none focus:border-primary"
                    >
                      {OPERATORS.map((operator) => (
                        <option key={operator.value} value={operator.value}>{operator.label}</option>
                      ))}
                    </select>
                    <input
                      value={ruleForm.value}
                      onChange={(event) => setRuleForm((prev) => ({ ...prev, value: event.target.value }))}
                      placeholder="值"
                      className={inputClassName}
                    />
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <select
                      value={ruleForm.action}
                      onChange={(event) => setRuleForm((prev) => ({ ...prev, action: event.target.value as ActionType }))}
                      className="h-10 rounded-md border bg-background px-2 text-sm outline-none focus:border-primary"
                    >
                      {ACTIONS.map((action) => <option key={action}>{action}</option>)}
                    </select>
                    <input
                      value={ruleForm.position_pct}
                      onChange={(event) => setRuleForm((prev) => ({ ...prev, position_pct: event.target.value }))}
                      placeholder="仓位%"
                      className={inputClassName}
                    />
                    <input
                      value={ruleForm.priority}
                      onChange={(event) => setRuleForm((prev) => ({ ...prev, priority: event.target.value }))}
                      placeholder="优先级"
                      className={inputClassName}
                    />
                  </div>
                  <button
                    onClick={addRule}
                    disabled={busy || !selectedTree || !ruleForm.name.trim()}
                    className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                  >
                    <Plus className="h-4 w-4" />
                    添加规则
                  </button>
                </div>
              </div>
            </section>

            <section className="grid grid-cols-1 gap-4 2xl:grid-cols-[minmax(0,1fr)_420px]">
              <div className="rounded-lg border bg-card">
                <SectionHeader icon={ListChecks} title="规则矩阵" meta={`${enabledRuleCount} 启用 / ${selectedRules.length} 总数`} />
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[780px] text-sm">
                    <thead className="border-b bg-muted/40 text-xs text-muted-foreground">
                      <tr>
                        <th className="px-3 py-2 text-left font-medium">规则</th>
                        <th className="px-3 py-2 text-left font-medium">条件</th>
                        <th className="px-3 py-2 text-left font-medium">动作</th>
                        <th className="px-3 py-2 text-left font-medium">仓位</th>
                        <th className="px-3 py-2 text-left font-medium">优先级</th>
                        <th className="px-3 py-2 text-right font-medium">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedRules.length === 0 ? (
                        <tr>
                          <td colSpan={6} className="px-3 py-10 text-center text-sm text-muted-foreground">
                            暂无规则，先从右上角新增一条。
                          </td>
                        </tr>
                      ) : (
                        selectedRules.map((rule) => (
                          <tr key={rule.id} className={cn("border-b last:border-0 hover:bg-muted/25", !rule.enabled && "opacity-55")}>
                            <td className="max-w-[240px] px-3 py-3">
                              <div className="font-medium">{rule.name}</div>
                              <div className="mt-1 truncate text-xs text-muted-foreground">{rule.description || rule.source || rule.created_by || rule.id}</div>
                            </td>
                            <td className="max-w-[340px] px-3 py-3 font-mono text-xs text-muted-foreground">
                              {rule.conditions.map(formatCondition).join(" && ") || "无条件"}
                            </td>
                            <td className="px-3 py-3">
                              <span className={cn("rounded px-2 py-1 text-xs", actionClass(rule.action))}>{rule.action}</span>
                            </td>
                            <td className="px-3 py-3">{rule.position_pct ?? "--"}%</td>
                            <td className="px-3 py-3">{rule.priority}</td>
                            <td className="px-3 py-3">
                              <div className="flex justify-end gap-1">
                                <button
                                  onClick={() => toggleRule(rule.id)}
                                  className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                                  aria-label={rule.enabled ? "停用规则" : "启用规则"}
                                >
                                  {rule.enabled ? <ToggleRight className="h-4 w-4 text-emerald-500" /> : <ToggleLeft className="h-4 w-4" />}
                                </button>
                                <button
                                  onClick={() => deleteRule(rule.id)}
                                  className="rounded p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                                  aria-label="删除规则"
                                >
                                  <Trash2 className="h-4 w-4" />
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="rounded-lg border bg-card">
                <SectionHeader icon={PlayCircle} title="评估" meta={evaluateResult?.matched ? "已命中规则" : "等待运行"} />
                <div className="space-y-3 p-3">
                  <textarea
                    value={contextText}
                    onChange={(event) => setContextText(event.target.value)}
                    spellCheck={false}
                    className="h-44 w-full resize-none rounded-md border bg-background p-3 font-mono text-xs outline-none focus:border-primary"
                  />
                  <button
                    onClick={evaluateTree}
                    disabled={busy || !selectedTree}
                    className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                  >
                    <PlayCircle className="h-4 w-4" />
                    运行评估
                  </button>
                  {evaluateResult && (
                    <div className="border-t pt-3">
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2 text-sm font-medium">
                          {evaluateResult.matched ? <CheckCircle2 className="h-4 w-4 text-emerald-500" /> : <AlertCircle className="h-4 w-4 text-muted-foreground" />}
                          {evaluateResult.matched ? "命中规则" : "未命中规则"}
                        </div>
                        <span className={cn("rounded px-2 py-1 text-xs", actionClass(evaluateResult.recommendation.action))}>
                          {evaluateResult.recommendation.action} · {evaluateResult.recommendation.position_pct}%
                        </span>
                      </div>
                      <p className="mt-2 text-sm text-muted-foreground">{evaluateResult.recommendation.message}</p>
                      {evaluateResult.rule && (
                        <div className="mt-3 rounded-md bg-muted/45 px-3 py-2 text-xs text-muted-foreground">
                          {evaluateResult.rule.name}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </section>
          </main>
        </div>
      </div>
    </div>
  );
}

function DecisionTreeCanvas({ visualization }: { visualization: DecisionTreeViz }) {
  const { nodes, edges } = visualization;
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const nodePositions = useMemo(() => calculateLayout(nodes, edges), [nodes, edges]);
  const bounds = useMemo(() => canvasBounds(nodePositions), [nodePositions]);
  const selected = nodes.find((node) => node.id === selectedNode);

  return (
    <div className="min-w-[760px]">
      <svg
        viewBox={`0 0 ${bounds.width} ${bounds.height}`}
        className="h-[460px] w-full rounded-md bg-[radial-gradient(circle_at_1px_1px,hsl(var(--border))_1px,transparent_0)] [background-size:18px_18px]"
      >
        <defs>
          <marker id="decision-arrow" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
            <polygon points="0 0, 10 3.5, 0 7" fill="hsl(var(--border))" />
          </marker>
        </defs>
        {edges.map((edge, index) => {
          const source = nodePositions[edge.source];
          const target = nodePositions[edge.target];
          if (!source || !target) return null;
          const midX = (source.x + target.x) / 2;
          const midY = (source.y + target.y) / 2;
          return (
            <g key={`${edge.source}-${edge.target}-${index}`}>
              <path
                d={`M ${source.x} ${source.y + 24} C ${source.x} ${midY}, ${target.x} ${midY}, ${target.x} ${target.y - 24}`}
                fill="none"
                stroke="hsl(var(--border))"
                strokeWidth={2}
                markerEnd="url(#decision-arrow)"
              />
              {edge.label && (
                <text x={midX} y={midY - 6} textAnchor="middle" className="fill-muted-foreground text-[10px]">
                  {truncate(edge.label, 18)}
                </text>
              )}
            </g>
          );
        })}

        {nodes.map((node) => {
          const pos = nodePositions[node.id];
          if (!pos) return null;
          const color = node.style?.color || "#6B7280";
          const isSelected = selectedNode === node.id;
          const disabled = node.enabled === false;
          const width = node.type === "root" ? 180 : node.type === "condition" ? 132 : 150;
          const height = node.type === "condition" ? 58 : 50;
          return (
            <g key={node.id} onClick={() => setSelectedNode(node.id)} className="cursor-pointer">
              {node.type === "condition" ? (
                <polygon
                  points={`${pos.x},${pos.y - height / 2} ${pos.x + width / 2},${pos.y} ${pos.x},${pos.y + height / 2} ${pos.x - width / 2},${pos.y}`}
                  fill={isSelected ? color : "hsl(var(--card))"}
                  stroke={color}
                  strokeWidth={isSelected ? 3 : 2}
                  opacity={disabled ? 0.5 : 1}
                />
              ) : (
                <rect
                  x={pos.x - width / 2}
                  y={pos.y - height / 2}
                  width={width}
                  height={height}
                  rx={8}
                  fill={isSelected ? color : "hsl(var(--card))"}
                  stroke={color}
                  strokeWidth={isSelected ? 3 : 2}
                  opacity={disabled ? 0.5 : 1}
                />
              )}
              <text
                x={pos.x}
                y={pos.y - 2}
                textAnchor="middle"
                className="text-[11px] font-medium"
                fill={isSelected ? "#fff" : "currentColor"}
              >
                {truncate(node.label, 16)}
              </text>
              {node.priority !== undefined && (
                <text x={pos.x} y={pos.y + 15} textAnchor="middle" className="fill-muted-foreground text-[10px]">
                  P{node.priority}
                </text>
              )}
            </g>
          );
        })}
      </svg>

      {selected && (
        <div className="mt-3 border-t pt-3">
          <div className="text-sm font-medium">{selected.label}</div>
          <div className="mt-1 text-sm text-muted-foreground">{selected.description || "暂无描述"}</div>
        </div>
      )}
    </div>
  );
}

function calculateLayout(nodes: DecisionTreeNode[], edges: DecisionTreeEdge[]): Record<string, { x: number; y: number }> {
  const positions: Record<string, { x: number; y: number }> = {};
  const children: Record<string, string[]> = {};
  edges.forEach((edge) => {
    children[edge.source] = [...(children[edge.source] ?? []), edge.target];
  });

  const targets = new Set(edges.map((edge) => edge.target));
  const root = nodes.find((node) => !targets.has(node.id));
  if (!root) return positions;

  const leafCount = (nodeId: string): number => {
    const childIds = children[nodeId] ?? [];
    if (childIds.length === 0) return 1;
    return childIds.reduce((sum, childId) => sum + leafCount(childId), 0);
  };

  let cursor = 90;
  const layout = (nodeId: string, depth: number): number => {
    const childIds = children[nodeId] ?? [];
    const y = 70 + depth * 120;
    if (childIds.length === 0) {
      const x = cursor;
      positions[nodeId] = { x, y };
      cursor += 170;
      return x;
    }
    const childXs = childIds.map((childId) => layout(childId, depth + 1));
    const x = childXs.reduce((sum, value) => sum + value, 0) / childXs.length;
    positions[nodeId] = { x, y };
    return x;
  };

  cursor = Math.max(90, leafCount(root.id) * 4);
  layout(root.id, 0);
  return positions;
}

function canvasBounds(positions: Record<string, { x: number; y: number }>) {
  const values = Object.values(positions);
  if (values.length === 0) return { width: 900, height: 420 };
  const maxX = Math.max(...values.map((pos) => pos.x));
  const maxY = Math.max(...values.map((pos) => pos.y));
  return { width: Math.max(900, maxX + 120), height: Math.max(420, maxY + 90) };
}

function parseConditionValue(value: string, operator: string): unknown {
  if (operator === "in" || operator === "not_in") {
    return value.split(",").map((item) => parseScalar(item.trim()));
  }
  if (operator === "between") {
    return value.split(",").map((item) => Number(item.trim())).slice(0, 2);
  }
  return parseScalar(value.trim());
}

function parseScalar(value: string): string | number | boolean {
  if (value === "true") return true;
  if (value === "false") return false;
  if (value !== "" && Number.isFinite(Number(value))) return Number(value);
  return value;
}

function formatCondition(condition: RuleCondition): string {
  const value = Array.isArray(condition.value) ? condition.value.join(", ") : String(condition.value);
  return `${condition.field} ${condition.operator} ${value}`;
}

function sentimentLabel(key: string): string {
  const labels: Record<string, string> = {
    limit_up_count: "涨停",
    limit_down_count: "跌停",
    max_limit_up_streak: "连板",
    broken_board_rate: "炸板率",
    up_down_ratio: "涨跌比",
    prev_limit_up_premium: "溢价",
  };
  return labels[key] ?? key;
}

function actionClass(action: ActionType): string {
  if (action === "买入" || action === "加仓") return "bg-emerald-500/10 text-emerald-600";
  if (action === "卖出" || action === "减仓" || action === "止损") return "bg-red-500/10 text-red-600";
  if (action === "止盈") return "bg-amber-500/10 text-amber-600";
  return "bg-muted text-muted-foreground";
}

function formatPercent(value: number | undefined): string {
  if (value === undefined) return "--";
  return `${(value * 100).toFixed(0)}%`;
}

function formatShortDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function truncate(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}
