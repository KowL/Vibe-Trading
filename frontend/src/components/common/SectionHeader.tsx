import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

interface SectionHeaderProps {
  icon?: LucideIcon;
  title: string;
  meta?: string;
  trailing?: React.ReactNode;
  className?: string;
  onClick?: () => void;
}

export function SectionHeader({ icon: Icon, title, meta, trailing, className, onClick }: SectionHeaderProps) {
  const interactive = Boolean(onClick);
  return (
    <div
      onClick={onClick}
      className={cn(
        "flex min-h-11 items-center justify-between gap-3 border-b px-3 py-2.5",
        interactive && "cursor-pointer select-none hover:bg-muted/40",
        className,
      )}
    >
      <h2 className="flex items-center gap-2 text-sm font-medium">
        {Icon && <Icon className="h-4 w-4 text-muted-foreground" />}
        {title}
      </h2>
      {(meta || trailing) && (
        <div className="flex items-center gap-2">
          {meta && <span className="truncate text-xs text-muted-foreground">{meta}</span>}
          {trailing}
        </div>
      )}
    </div>
  );
}
