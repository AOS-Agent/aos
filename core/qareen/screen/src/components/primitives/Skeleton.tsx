import type { CSSProperties } from 'react';

export function Skeleton({ className = '', style }: { className?: string; style?: CSSProperties }) {
  return (
    <div
      className={`animate-pulse bg-bg-tertiary rounded-[4px] ${className}`}
      style={style}
    />
  );
}

export function SkeletonRows({ count = 4 }: { count?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 h-9 px-3 -mx-3">
          <Skeleton className="w-1.5 h-1.5 rounded-full" />
          <Skeleton className="h-3 flex-1" style={{ maxWidth: `${60 + (i * 17 % 30)}%` }} />
          <Skeleton className="h-3 w-8" />
        </div>
      ))}
    </div>
  );
}

export function SkeletonCards({ count = 3 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="bg-bg-secondary rounded-[7px] p-5">
          <div className="flex items-center gap-2.5 mb-3">
            <Skeleton className="w-4 h-4 rounded-full" />
            <Skeleton className="h-4 w-24" />
          </div>
          <Skeleton className="h-3 w-full mb-1.5" />
          <Skeleton className="h-3 w-3/4 mb-4" />
          <div className="flex gap-2">
            <Skeleton className="h-5 w-12 rounded-[3px]" />
            <Skeleton className="h-5 w-16 rounded-[3px]" />
          </div>
        </div>
      ))}
    </div>
  );
}
