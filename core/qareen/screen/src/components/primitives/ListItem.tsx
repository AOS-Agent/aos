import type { ReactNode, HTMLAttributes } from "react";
import { ChevronRight } from "lucide-react";

interface ListItemProps extends HTMLAttributes<HTMLDivElement> {
  title: string;
  subtitle?: string;
  leading?: ReactNode;
  trailing?: ReactNode;
  meta?: string;
  chevron?: boolean;
  active?: boolean;
}

export function ListItem({
  title,
  subtitle,
  leading,
  trailing,
  meta,
  chevron = false,
  active = false,
  className = "",
  ...props
}: ListItemProps) {
  return (
    <div
      className={`
        group flex items-center gap-3
        h-9 px-3 -mx-3 rounded-sm
        transition-colors cursor-pointer
        hover:bg-hover
        ${active ? "bg-active" : ""}
        ${className}
      `}
      style={{ transitionDuration: "var(--duration-instant)" }}
      {...props}
    >
      {leading && (
        <span className="shrink-0 [&>svg]:w-3.5 [&>svg]:h-3.5 text-text-quaternary">
          {leading}
        </span>
      )}

      <div className="flex-1 min-w-0 flex items-baseline gap-2">
        <span className="text-[13px] font-[510] text-text-secondary truncate">
          {title}
        </span>
        {subtitle && (
          <span className="text-[11px] text-text-quaternary truncate shrink-0">
            {subtitle}
          </span>
        )}
      </div>

      {meta && (
        <span className="text-[10px] text-text-quaternary shrink-0">
          {meta}
        </span>
      )}

      {trailing}

      {chevron && (
        <ChevronRight className="w-3.5 h-3.5 text-text-quaternary opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
      )}
    </div>
  );
}
