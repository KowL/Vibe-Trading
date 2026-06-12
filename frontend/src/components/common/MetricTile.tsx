import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

interface MetricTileProps {
  icon?: LucideIcon;
  label: string;
  value: string | number;
  hint?: string;
  tone?: string;
  className?: string;
}

export function MetricTile({ icon: Icon, label, value, hint, tone, className }: MetricTileProps) {
  return (
    <div className={cn("flex min-h-20 items-center gap-3 px-4 py-3", className)}>
      {Icon && (
        <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md border bg-background">
          <Icon className={cn("h-4 w-4", tone ?? "text-muted-foreground")} />
        </span>
      )}
      <div className="min-w-0">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className={cn("mt-0.5 truncate text-lg font-semibold", tone)}>{value}</div>
        {hint && <div className="mt-0.5 truncate text-[11px] text-muted-foreground/80">{hint}</div>}
      </div>
    </div>
  );
}
