"use client";

import type { HTMLAttributes, ReactNode } from "react";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  hover?: boolean;
}

export function Card({
  children,
  hover = false,
  className = "",
  ...props
}: CardProps) {
  return (
    <div
      className={`
        bg-bg-secondary rounded border border-border p-4
        ${hover ? "cursor-pointer transition-colors duration-100 hover:border-border-secondary" : ""}
        ${className}
      `}
      {...props}
    >
      {children}
    </div>
  );
}
