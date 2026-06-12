import { useCallback, useEffect, useState } from "react";
import {
  CalendarRange,
  Check,
  Copy,
  FileText,
  Loader2,
  Newspaper,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { SectionHeader } from "@/components/common/SectionHeader";

const KIND_LABELS: Record<string, string> = {
  open: "开盘报告",
  close: "收盘复盘",
  weekly: "周度复盘",
};

const KIND_DESCRIPTIONS: Record<string, string> = {
  open: "盘前资讯与集合竞价关键信息",
  close: "当日盘面总结、热点板块与涨停梯队复盘",
  weekly: "本周主线、连板高度与情绪周期回顾",
};

const remarkPlugins = [remarkGfm];
const rehypePlugins = [rehypeHighlight];

const PROSE_CLASSES =
  "prose prose-sm dark:prose-invert max-w-none leading-relaxed " +
  "prose-table:border prose-table:border-border/50 " +
  "prose-th:bg-muted/30 prose-th:px-3 prose-th:py-1.5 " +
  "prose-td:px-3 prose-td:py-1.5 prose-th:text-left " +
  "prose-th:text-xs prose-th:font-medium prose-td:text-xs " +
  "prose-hr:hidden prose-headings:scroll-mt-20";

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      },
      () => {
        // Fallback: select via temporary textarea
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        try {
          document.execCommand("copy");
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        } finally {
          document.body.removeChild(ta);
        }
      },
    );
  }, [text]);

  return (
    <button
      onClick={handleCopy}
      className="inline-flex h-7 items-center gap-1.5 rounded-md border bg-background px-2 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
      title={copied ? "已复制" : "复制 Markdown 源码"}
    >
      {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
      {copied ? "已复制" : "复制"}
    </button>
  );
}

export function ReportPage() {
  const [kind, setKind] = useState("close");
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [markdown, setMarkdown] = useState("");
  const [title, setTitle] = useState("");
  const [createdAt, setCreatedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [showSource, setShowSource] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const data = await api.getReport(kind, date);
      setMarkdown(data.markdown);
      setTitle(data.title);
      setCreatedAt(data.created_at || null);
    } catch {
      setMarkdown("");
      setTitle("");
      setCreatedAt(null);
    } finally {
      setLoading(false);
    }
  };

  const generate = async () => {
    setGenerating(true);
    try {
      const data = await api.generateReport(kind, date);
      setMarkdown(data.markdown);
      setTitle(data.title);
      setCreatedAt(data.created_at || null);
    } finally {
      setGenerating(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, date]);

  const headerTitle = title || `${KIND_LABELS[kind] ?? kind} — ${date}`;
  const status = loading
    ? "加载中…"
    : markdown
      ? showSource
        ? "已生成 · 源码视图"
        : "已生成"
      : "未生成";

  return (
    <div className="min-h-full bg-background">
      <div className="mx-auto max-w-[1500px] p-3 md:p-5">
        <div className="mb-4 overflow-hidden rounded-lg border bg-card">
          <div className="border-b bg-muted/25 px-4 py-3">
            <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="inline-flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary">
                    <Newspaper className="h-4 w-4" />
                  </span>
                  <h1 className="text-xl font-semibold tracking-normal">市场报告</h1>
                  <span className="rounded border bg-background px-2 py-1 text-xs text-muted-foreground">
                    {KIND_LABELS[kind] ?? kind} · {date}
                  </span>
                  {createdAt && (
                    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                      <CalendarRange className="h-3.5 w-3.5" />
                      {createdAt.replace("T", " ").slice(0, 16)}
                    </span>
                  )}
                </div>
                <p className="mt-1 truncate text-sm text-muted-foreground">
                  {KIND_DESCRIPTIONS[kind] ?? "AI 驱动的盘面复盘报告"}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={kind}
                  onChange={(e) => setKind(e.target.value)}
                  className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                >
                  {Object.entries(KIND_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
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
                  onClick={generate}
                  disabled={generating}
                  className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-3 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                  {generating ? "生成中…" : "生成报告"}
                </button>
              </div>
            </div>
          </div>
        </div>

        <section className="overflow-hidden rounded-lg border bg-card">
          <SectionHeader
            icon={FileText}
            title={headerTitle}
            meta={status}
            trailing={
              markdown ? (
                <div className="flex items-center gap-2">
                  <div className="flex items-center rounded-md border bg-background p-0.5 text-xs">
                    <button
                      onClick={() => setShowSource(false)}
                      className={cn(
                        "h-7 rounded-sm px-2.5 transition-colors",
                        !showSource ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      渲染
                    </button>
                    <button
                      onClick={() => setShowSource(true)}
                      className={cn(
                        "h-7 rounded-sm px-2.5 transition-colors",
                        showSource ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      源码
                    </button>
                  </div>
                  <CopyButton text={markdown} />
                </div>
              ) : null
            }
          />
          <div className="p-4">
            {loading ? (
              <div className="py-12 text-center text-sm text-muted-foreground">
                <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin" />
                加载中…
              </div>
            ) : markdown ? (
              showSource ? (
                <pre className="max-h-[70vh] overflow-auto rounded-md border bg-muted/30 p-3 font-mono text-xs leading-relaxed">
                  <code>{markdown}</code>
                </pre>
              ) : (
                <div className={PROSE_CLASSES}>
                  <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins}>
                    {markdown}
                  </ReactMarkdown>
                </div>
              )
            ) : (
              <div className="rounded-lg border border-dashed p-12 text-center">
                <FileText className="mx-auto mb-2 h-6 w-6 text-muted-foreground/60" />
                <p className="text-sm text-muted-foreground">该日期暂无报告</p>
                <p className="mt-1 text-xs text-muted-foreground/70">点击「生成报告」AI 写一份</p>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
