import { ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";

function getXueqiuUrl(symbol: string): string | null {
  const parts = symbol.trim().split(".");
  if (parts.length !== 2) return null;
  const [code, exchange] = parts;
  const upperExchange = exchange.toUpperCase();
  if (upperExchange !== "SH" && upperExchange !== "SZ" && upperExchange !== "BJ") return null;
  return `https://xueqiu.com/S/${upperExchange}${code}`;
}

interface StockLinkProps {
  symbol: string;
  children?: React.ReactNode;
  className?: string;
  showIcon?: boolean;
}

export function StockLink({ symbol, children, className, showIcon = true }: StockLinkProps) {
  const url = getXueqiuUrl(symbol);
  if (!url) {
    return <span className={className}>{children ?? symbol}</span>;
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        "inline-flex items-center gap-1 hover:text-primary hover:underline",
        className
      )}
      title={`在雪球查看 ${symbol}`}
      onClick={(e) => e.stopPropagation()}
    >
      {children ?? symbol}
      {showIcon && <ExternalLink className="h-3 w-3 opacity-60" />}
    </a>
  );
}
