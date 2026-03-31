import type { ReactNode } from "react";

interface EmptyStateProps {
  icon: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className = "",
}: EmptyStateProps) {
  return (
    <div
      className={`flex flex-col items-center justify-center py-16 ${className}`}
    >
      <span className="text-text-quaternary opacity-30 mb-3 [&>svg]:w-10 [&>svg]:h-10">
        {icon}
      </span>
      <p className="text-[14px] font-[510] text-text-tertiary mb-1">
        {title}
      </p>
      {description && (
        <p className="text-[12px] text-text-quaternary max-w-[280px] text-center">
          {description}
        </p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
