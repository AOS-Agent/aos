import type { ButtonHTMLAttributes, ReactNode } from "react";

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon: ReactNode;
  tooltip?: string;
  active?: boolean;
}

export function IconButton({
  icon,
  tooltip,
  active = false,
  className = "",
  ...props
}: IconButtonProps) {
  return (
    <button
      title={tooltip}
      aria-label={tooltip}
      className={`
        inline-flex items-center justify-center
        w-8 h-8 rounded-sm
        text-text-tertiary transition-colors duration-100
        hover:bg-hover hover:text-text-secondary
        active:bg-active
        disabled:opacity-40 disabled:pointer-events-none
        ${active ? "bg-active text-text" : ""}
        ${className}
      `}
      {...props}
    >
      <span className="[&>svg]:w-4 [&>svg]:h-4">{icon}</span>
    </button>
  );
}
