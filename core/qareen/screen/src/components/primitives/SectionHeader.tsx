import type { ReactNode } from "react";

interface SectionHeaderProps {
  label: string;
  icon?: ReactNode;
  count?: number;
  action?: ReactNode;
  className?: string;
}

export function SectionHeader({
  label,
  icon,
  count,
  action,
  className = "",
}: SectionHeaderProps) {
  return (
    <div className={`flex items-center gap-2 mb-3 ${className}`}>
      {icon && (
        <span className="text-text-quaternary shrink-0 [&>svg]:w-3.5 [&>svg]:h-3.5">
          {icon}
        </span>
      )}
      <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">
        {label}
      </span>
      {count !== undefined && count > 0 && (
        <span className="text-[10px] font-[510] text-text-quaternary bg-bg-tertiary rounded-xs px-1.5 py-0.5 leading-none">
          {count}
        </span>
      )}
      {action && <div className="ml-auto">{action}</div>}
    </div>
  );
}
