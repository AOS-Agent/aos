export type StatusDotColor =
  | "green"
  | "red"
  | "yellow"
  | "orange"
  | "blue"
  | "purple"
  | "gray";

export type StatusDotSize = "sm" | "md";

interface StatusDotProps {
  color?: StatusDotColor;
  size?: StatusDotSize;
  pulse?: boolean;
  label?: string;
  className?: string;
}

const colorMap: Record<StatusDotColor, string> = {
  green: "bg-green",
  red: "bg-red",
  yellow: "bg-yellow",
  orange: "bg-orange",
  blue: "bg-blue",
  purple: "bg-purple",
  gray: "bg-text-quaternary",
};

const labelColorMap: Record<StatusDotColor, string> = {
  green: "text-green",
  red: "text-red",
  yellow: "text-yellow",
  orange: "text-orange",
  blue: "text-blue",
  purple: "text-purple",
  gray: "text-text-quaternary",
};

export function StatusDot({
  color = "gray",
  size = "sm",
  pulse = false,
  label,
  className = "",
}: StatusDotProps) {
  const dotSize = size === "sm" ? "w-1.5 h-1.5" : "w-[6px] h-[6px]";

  if (label) {
    return (
      <span className={`inline-flex items-center gap-1.5 ${className}`}>
        <span
          className={`${dotSize} rounded-full shrink-0 ${colorMap[color]} ${
            pulse ? "animate-pulse" : ""
          }`}
        />
        <span className={`text-[10px] font-[510] ${labelColorMap[color]}`}>
          {label}
        </span>
      </span>
    );
  }

  return (
    <span
      className={`${dotSize} rounded-full shrink-0 ${colorMap[color]} ${
        pulse ? "animate-pulse" : ""
      } ${className}`}
    />
  );
}
