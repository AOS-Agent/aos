/**
 * Shared chrome for the project brief — the small pieces every section reuses.
 * Kept in one file so section spacing and label weight can't drift apart.
 */

import type { ReactNode } from 'react';
import { stateIsHollow, stateLabel, stateTone, type Tone } from './briefStyle';

/** Section label. Sans, tiny, uppercase — the one place DESIGN allows caps. */
export function Overline({ children, count }: { children: ReactNode; count?: number }) {
  return (
    <div className="flex items-center gap-2 mb-2.5">
      <span className="text-[11px] font-[590] uppercase tracking-[0.07em] text-text-tertiary">
        {children}
      </span>
      {count != null && count > 0 && (
        <span className="text-[11px] font-mono text-text-quaternary tabular-nums">{count}</span>
      )}
    </div>
  );
}

/** Derived-state pill. Filled for live states, outlined for absence. */
export function StateChip({ state, size = 'md' }: { state: string; size?: 'sm' | 'md' }) {
  const tone = stateTone(state);
  const hollow = stateIsHollow(state);
  const h = size === 'sm' ? 'h-[18px] text-[10px] px-1.5 gap-1' : 'h-[22px] text-[11px] px-2 gap-1.5';
  return (
    <span
      className={`inline-flex items-center rounded-full font-[510] whitespace-nowrap ${h} ${tone.text} ${
        hollow ? 'border border-border-secondary' : tone.bg
      }`}
    >
      <span className={`w-[5px] h-[5px] rounded-full shrink-0 ${tone.dot}`} />
      {stateLabel(state)}
    </span>
  );
}

/** Kind pill for artifacts, conflicts and phases. */
export function KindChip({ label, tone }: { label: string; tone: Tone }) {
  return (
    <span
      className={`inline-flex items-center h-[18px] px-1.5 rounded-xs text-[10px] font-[510] tracking-[0.02em] whitespace-nowrap ${tone.text} ${tone.bg}`}
    >
      {label}
    </span>
  );
}

/** Thin progress rail. `barClass` carries the area tone so phases match the project. */
export function ProgressRail({ done, total, barClass }: { done: number; total: number; barClass: string }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div className="flex-1 h-1 bg-bg-tertiary rounded-full overflow-hidden min-w-[40px]">
      <div
        className={`h-full rounded-full transition-[width] ${barClass}`}
        style={{ width: `${pct}%`, transitionDuration: 'var(--duration-normal)' }}
      />
    </div>
  );
}

/** Monospace reference chip — task ids and file paths inside conflicts. */
export function RefChip({ label, onClick, title }: { label: string; onClick?: () => void; title?: string }) {
  const base =
    'inline-flex items-center h-[20px] px-1.5 rounded-xs bg-bg-tertiary text-[11px] font-mono text-text-tertiary max-w-[260px] truncate';
  if (!onClick) return <span className={base} title={title ?? label}>{label}</span>;
  return (
    <button
      onClick={onClick}
      title={title ?? label}
      className={`${base} hover:bg-bg-quaternary hover:text-text-secondary cursor-pointer transition-colors`}
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      {label}
    </button>
  );
}
