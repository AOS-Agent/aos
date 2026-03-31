import type { ReactNode } from "react";

export type TagColor =
  | "gray"
  | "green"
  | "blue"
  | "purple"
  | "red"
  | "yellow"
  | "pink"
  | "orange"
  | "teal";

export type TagSize = "sm" | "md";

interface TagProps {
  label: string;
  color?: TagColor;
  size?: TagSize;
  icon?: ReactNode;
  className?: string;
}

const colorMap: Record<TagColor, { text: string; bg: string }> = {
  gray: { text: "text-tag-gray", bg: "bg-tag-gray-bg" },
  green: { text: "text-tag-green", bg: "bg-tag-green-bg" },
  blue: { text: "text-tag-blue", bg: "bg-tag-blue-bg" },
  purple: { text: "text-tag-purple", bg: "bg-tag-purple-bg" },
  red: { text: "text-tag-red", bg: "bg-tag-red-bg" },
  yellow: { text: "text-tag-yellow", bg: "bg-tag-yellow-bg" },
  pink: { text: "text-tag-pink", bg: "bg-tag-pink-bg" },
  orange: { text: "text-tag-orange", bg: "bg-tag-orange-bg" },
  teal: { text: "text-tag-teal", bg: "bg-tag-teal-bg" },
};

export function Tag({
  label,
  color = "gray",
  size = "sm",
  icon,
  className = "",
}: TagProps) {
  const { text, bg } = colorMap[color];
  const height = size === "sm" ? "h-5" : "h-6";

  return (
    <span
      className={`
        inline-flex items-center gap-1 px-2 rounded-xs
        text-[11px] font-medium leading-[1.2]
        ${height} ${text} ${bg} ${className}
      `}
    >
      {icon && <span className="shrink-0 [&>svg]:w-3 [&>svg]:h-3">{icon}</span>}
      {label}
    </span>
  );
}
