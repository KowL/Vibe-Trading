import { cn } from "@/lib/utils";

interface MiniStatProps {
  label: string;
  value: React.ReactNode;
  className?: string;
  tone?: string;
}

export function MiniStat({ label, value, className, tone }: MiniStatProps) {
  return (
    <div className={cn("rounded-md border bg-background px-2 py-1.5", className)}>
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className={cn("mt-0.5 font-medium", tone)}>{value ?? "--"}</div>
    </div>
  );
}
