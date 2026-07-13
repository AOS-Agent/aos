/**
 * BranchGauge — Zone 1, the instrument header.
 *
 * One sentence + one gauge (no stat dump): "council-substrate is 150 commits
 * ahead of main, 1 behind — 12 of 14 batches cleared to ship." Branch in mono,
 * a thin ahead/behind bar (ahead in tag-green, behind in text-quaternary),
 * counts in mono tabular-nums, and a ghost manual Refresh (no auto-poll).
 */

import { RefreshCw } from 'lucide-react';
import type { GitStatus } from '@/lib/gitApi';

interface BranchGaugeProps {
  status?: GitStatus;
  loading: boolean;
  /** "<decided> of <total>" ship readiness, when the plan is loaded. */
  clearedLabel?: string;
  onRefresh: () => void;
  refreshing?: boolean;
}

export default function BranchGauge({
  status,
  loading,
  clearedLabel,
  onRefresh,
  refreshing,
}: BranchGaugeProps) {
  const branch = status?.branch ?? '—';
  const ahead = status?.ahead ?? 0;
  const behind = status?.behind ?? 0;
  const base = status?.base ?? 'main';
  const baseShort = base?.replace(/^origin\//, '') ?? 'main';

  // Gauge proportions — ahead fills against behind on a shared track.
  const span = Math.max(1, ahead + behind);
  const aheadPct = Math.round((ahead / span) * 100);

  const sentence = loading ? (
    'Reading the repo…'
  ) : status?.base_missing ? (
    <>
      <span className="font-mono text-text-secondary">{branch}</span> has no{' '}
      <span className="font-mono">{baseShort}</span> base to compare against.
    </>
  ) : (
    <>
      <span className="font-mono text-text-secondary">{branch}</span> is{' '}
      <span className="font-mono tabular-nums text-tag-green">{ahead}</span>{' '}
      {ahead === 1 ? 'commit' : 'commits'} ahead of{' '}
      <span className="font-mono">{baseShort}</span>
      {behind > 0 && (
        <>
          ,{' '}
          <span className="font-mono tabular-nums text-text-tertiary">
            {behind}
          </span>{' '}
          behind
        </>
      )}
      {clearedLabel ? <> — {clearedLabel}</> : '.'}
    </>
  );

  return (
    <div className="mb-6">
      <div className="flex items-start justify-between gap-4">
        <p className="text-[15px] leading-[1.6] text-text-secondary">{sentence}</p>
        <button
          onClick={onRefresh}
          disabled={refreshing}
          aria-label="Refresh git state"
          className="shrink-0 flex items-center gap-1.5 h-7 px-2.5 rounded-full text-[12px] text-text-quaternary hover:text-text-tertiary border border-border hover:border-border-secondary cursor-pointer disabled:opacity-50"
          style={{ transitionDuration: 'var(--duration-instant)' }}
        >
          <RefreshCw className={`w-3 h-3 ${refreshing ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Gauge — thin ahead/behind bar */}
      <div className="mt-3 flex items-center gap-2.5">
        <div className="flex-1 h-1.5 rounded-full overflow-hidden flex bg-bg-tertiary">
          <div
            className="h-full bg-tag-green"
            style={{ width: `${aheadPct}%`, transition: 'width var(--duration-normal) ease-out' }}
          />
          <div
            className="h-full"
            style={{ width: `${100 - aheadPct}%`, background: 'var(--color-text-quaternary)', opacity: 0.4 }}
          />
        </div>
        <span className="shrink-0 text-[12px] font-mono tabular-nums text-text-quaternary">
          <span className="text-tag-green">+{ahead}</span>
          {' / '}
          <span className="text-text-tertiary">-{behind}</span>
        </span>
      </div>
    </div>
  );
}
