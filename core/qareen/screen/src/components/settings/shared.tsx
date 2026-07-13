import type { LucideIcon } from 'lucide-react';
import { Skeleton } from '@/components/primitives';

// ---------------------------------------------------------------------------
// Shared settings primitives — used across all section components.
// Extracted from the original Settings.tsx for the section-registry pattern.
// ---------------------------------------------------------------------------

/* ── Setting Row ── */
export function SettingRow({
  label,
  value,
  description,
  trailing,
}: {
  label: string;
  value?: string | React.ReactNode;
  description?: string;
  trailing?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between py-3 min-h-[44px]">
      <div className="flex-1 min-w-0 pr-4">
        <span className="text-[13px] font-[510] text-text-secondary block">{label}</span>
        {description && (
          <span className="text-[12px] text-text-quaternary block mt-0.5">{description}</span>
        )}
      </div>
      {trailing ?? (
        <span className="text-[13px] text-text-tertiary shrink-0 text-right max-w-[50%] truncate">
          {value ?? '\u2014'}
        </span>
      )}
    </div>
  );
}

/* ── Section Wrapper ── */
export function SettingCard({
  icon: Icon,
  title,
  action,
  children,
}: {
  icon?: LucideIcon;
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-8">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          {Icon && <Icon className="w-4 h-4 text-text-quaternary" />}
          <h2 className="text-[13px] font-[590] text-text tracking-[-0.01em]">{title}</h2>
        </div>
        {action}
      </div>
      <div className="divide-y divide-border">
        {children}
      </div>
    </section>
  );
}

/* ── Toggle Switch ── */
export function Toggle({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`
        relative shrink-0 w-[42px] h-[26px] rounded-full cursor-pointer
        transition-colors duration-[80ms]
        ${checked ? 'bg-accent' : 'bg-bg-quaternary'}
      `}
    >
      <span
        className={`
          absolute top-1/2 -translate-y-1/2 w-[20px] h-[20px] rounded-full bg-white
          shadow-[0_1px_3px_rgba(0,0,0,0.3)]
          transition-[left] duration-[80ms]
          ${checked ? 'left-[19px]' : 'left-[3px]'}
        `}
      />
    </button>
  );
}

/* ── Loading Placeholder Rows ── */
export function LoadingRows({ count = 3 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="flex items-center justify-between py-3 min-h-[44px]">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 w-32" />
        </div>
      ))}
    </>
  );
}
