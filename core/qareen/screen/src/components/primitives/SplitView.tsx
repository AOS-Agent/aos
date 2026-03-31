import type { ReactNode } from "react";

interface SplitViewProps {
  left: ReactNode;
  right: ReactNode;
  ratio?: "1/2" | "1/3" | "2/3" | "3/5" | "2/5";
  rightWidth?: number;
  divider?: boolean;
  className?: string;
}

const ratioClasses: Record<string, { left: string; right: string }> = {
  "1/2": { left: "flex-1", right: "flex-1" },
  "1/3": { left: "w-1/3 shrink-0", right: "flex-1" },
  "2/3": { left: "flex-1", right: "w-1/3 shrink-0" },
  "3/5": { left: "w-3/5 shrink-0", right: "flex-1" },
  "2/5": { left: "w-2/5 shrink-0", right: "flex-1" },
};

export function SplitView({
  left,
  right,
  ratio = "1/2",
  rightWidth,
  divider = true,
  className = "",
}: SplitViewProps) {
  const classes = ratioClasses[ratio] || ratioClasses["1/2"];

  return (
    <div className={`flex min-h-0 ${className}`}>
      <div
        className={`overflow-y-auto ${
          rightWidth ? "flex-1" : classes.left
        } ${divider ? "border-r border-border" : ""}`}
      >
        {left}
      </div>
      <div
        className={`overflow-y-auto ${rightWidth ? "shrink-0" : classes.right}`}
        style={rightWidth ? { width: rightWidth } : undefined}
      >
        {right}
      </div>
    </div>
  );
}
