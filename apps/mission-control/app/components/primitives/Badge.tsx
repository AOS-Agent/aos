"use client";

export type BadgeStatus = "success" | "warning" | "error" | "info" | "neutral";

interface BadgeProps {
  label: string;
  status?: BadgeStatus;
  className?: string;
}

const statusStyles: Record<
  BadgeStatus,
  { dot: string; text: string; bg: string }
> = {
  success: {
    dot: "bg-green",
    text: "text-green",
    bg: "bg-green-muted",
  },
  warning: {
    dot: "bg-yellow",
    text: "text-yellow",
    bg: "bg-yellow-muted",
  },
  error: {
    dot: "bg-red",
    text: "text-red",
    bg: "bg-red-muted",
  },
  info: {
    dot: "bg-blue",
    text: "text-blue",
    bg: "bg-blue-muted",
  },
  neutral: {
    dot: "bg-tag-gray",
    text: "text-tag-gray",
    bg: "bg-tag-gray-bg",
  },
};

export function Badge({
  label,
  status = "neutral",
  className = "",
}: BadgeProps) {
  const { dot, text, bg } = statusStyles[status];

  return (
    <span
      className={`
        inline-flex items-center gap-1.5 px-2 h-5
        rounded-xs text-[11px] font-medium leading-[1.2]
        ${text} ${bg} ${className}
      `}
    >
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
      {label}
    </span>
  );
}
