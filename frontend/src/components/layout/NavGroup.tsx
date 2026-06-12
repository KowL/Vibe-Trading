import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { LucideIcon } from "lucide-react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

export interface NavChild {
  to: string;
  label: string;
  icon?: LucideIcon;
}

interface NavGroupProps {
  to: string;
  icon: LucideIcon;
  label: string;
  pathname: string;
  collapsed: boolean;
  children: NavChild[];
  defaultOpen?: boolean;
  storageKey?: string;
}

export function NavGroup({
  to,
  icon: Icon,
  label,
  pathname,
  collapsed,
  children,
  defaultOpen = false,
  storageKey,
}: NavGroupProps) {
  const isActive = pathname === to || pathname.startsWith(to + "/");
  const initial = (() => {
    if (!storageKey) return defaultOpen || isActive;
    const stored = localStorage.getItem(storageKey);
    if (stored === "open") return true;
    if (stored === "closed") return false;
    return defaultOpen || isActive;
  })();
  const [open, setOpen] = useState<boolean>(initial);

  useEffect(() => {
    if (isActive) setOpen(true);
  }, [isActive]);

  const persist = (next: boolean) => {
    setOpen(next);
    if (storageKey) {
      localStorage.setItem(storageKey, next ? "open" : "closed");
    }
  };

  if (collapsed) {
    return (
      <Link
        to={to}
        className={cn(
          "flex items-center justify-center rounded-md p-2 text-sm transition-colors",
          isActive
            ? "bg-primary/10 text-primary font-medium"
            : "text-muted-foreground hover:bg-muted hover:text-foreground",
        )}
        title={label}
      >
        <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
      </Link>
    );
  }

  return (
    <div className="space-y-0.5">
      <button
        type="button"
        onClick={() => persist(!open)}
        className={cn(
          "flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
          isActive
            ? "bg-primary/10 text-primary font-medium"
            : "text-muted-foreground hover:bg-muted hover:text-foreground",
        )}
        aria-expanded={open}
      >
        <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span className="flex-1 text-left">{label}</span>
        {open ? <ChevronDown className="h-3.5 w-3.5 opacity-70" /> : <ChevronRight className="h-3.5 w-3.5 opacity-70" />}
      </button>
      {open && (
        <div className="ml-3 space-y-0.5 border-l border-border pl-3">
          {children.map(({ to: childTo, label: childLabel, icon: ChildIcon }) => {
            const childActive = pathname === childTo;
            return (
              <Link
                key={childTo}
                to={childTo}
                className={cn(
                  "flex items-center gap-2 rounded-md py-1.5 pl-2 pr-2 text-xs transition-colors",
                  childActive
                    ? "bg-primary/10 text-primary font-medium"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                {ChildIcon ? (
                  <ChildIcon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                ) : (
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current opacity-50" />
                )}
                {childLabel}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

export type { NavGroupProps };
