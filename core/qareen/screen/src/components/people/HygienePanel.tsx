import { useState } from 'react';
import { ShieldCheck, Loader2, ArrowRight, X, RefreshCw } from 'lucide-react';
import { useHygieneQueue, useHygieneStats, useApproveHygiene, useRejectHygiene, useRunHygieneScan } from '@/hooks/usePeople';
import { Tag } from '@/components/primitives/Tag';
import { EmptyState } from '@/components/primitives/EmptyState';
import { SkeletonRows } from '@/components/primitives/Skeleton';
import type { HygieneIssueResponse } from '@/lib/types';

// ---------------------------------------------------------------------------
// Action type labels and colors
// ---------------------------------------------------------------------------

const ACTION_COLORS: Record<string, 'orange' | 'red' | 'blue' | 'purple' | 'gray'> = {
  merge: 'orange',
  archive: 'red',
  review: 'blue',
  enrich: 'purple',
  dedup: 'orange',
};

function actionColor(type: string) {
  return ACTION_COLORS[type.toLowerCase()] || 'gray';
}

// ---------------------------------------------------------------------------
// Confidence indicator
// ---------------------------------------------------------------------------

function ConfidencePill({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <span className="text-[10px] text-text-quaternary tabular-nums bg-bg-tertiary px-2 py-0.5 rounded-full">
      {pct}%
    </span>
  );
}

// ---------------------------------------------------------------------------
// Issue row
// ---------------------------------------------------------------------------

function IssueRow({ issue }: {
  issue: HygieneIssueResponse;
}) {
  const approve = useApproveHygiene();
  const reject = useRejectHygiene();
  const [rejecting, setRejecting] = useState(false);

  const isPending = approve.isPending || reject.isPending;

  return (
    <div className="flex items-center gap-3 px-3 py-2.5 rounded-[5px] bg-bg-secondary/50 border border-border transition-colors hover:border-border-secondary" style={{ transitionDuration: 'var(--duration-instant)' }}>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <Tag label={issue.action_type} color={actionColor(issue.action_type)} size="sm" />
          <ConfidencePill value={issue.confidence} />
        </div>
        <div className="flex items-center gap-1.5 text-[12px]">
          {issue.person_a_name && (
            <span className="text-text-secondary font-[510] truncate max-w-[140px]">{issue.person_a_name}</span>
          )}
          {issue.person_b_name && (
            <>
              <ArrowRight className="w-3 h-3 text-text-quaternary shrink-0" />
              <span className="text-text-secondary font-[510] truncate max-w-[140px]">{issue.person_b_name}</span>
            </>
          )}
        </div>
        {issue.reason && (
          <p className="text-[11px] text-text-quaternary mt-0.5 truncate">{issue.reason}</p>
        )}
      </div>

      <div className="flex items-center gap-1.5 shrink-0">
        <button
          onClick={() => approve.mutate(issue.id)}
          disabled={isPending}
          className="flex items-center gap-1 px-3 py-1 rounded-full bg-green/10 text-green text-[11px] font-[510] hover:bg-green/20 transition-colors cursor-pointer disabled:opacity-40"
          style={{ transitionDuration: 'var(--duration-instant)' }}
        >
          {approve.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Approve'}
        </button>
        <button
          onClick={() => {
            setRejecting(true);
            reject.mutate({ issueId: issue.id }, {
              onSettled: () => setRejecting(false),
            });
          }}
          disabled={isPending}
          className="flex items-center justify-center w-7 h-7 rounded-full text-text-quaternary hover:bg-hover hover:text-text-tertiary transition-colors cursor-pointer disabled:opacity-40"
          style={{ transitionDuration: 'var(--duration-instant)' }}
        >
          {rejecting ? <Loader2 className="w-3 h-3 animate-spin" /> : <X className="w-3 h-3" />}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// HygienePanel — full view
// ---------------------------------------------------------------------------

export default function HygienePanel() {
  const { data: statsData, isLoading: statsLoading } = useHygieneStats();
  const { data: queueData, isLoading: queueLoading } = useHygieneQueue();
  const runScan = useRunHygieneScan();

  const stats = statsData;
  const issues = queueData?.issues ?? [];

  return (
    <div>
      {/* Stats row */}
      <div className="flex items-center gap-3 mb-4">
        {statsLoading ? (
          <div className="flex gap-2">
            {[1, 2, 3].map(i => <div key={i} className="w-16 h-5 bg-bg-secondary rounded-xs animate-pulse" />)}
          </div>
        ) : stats ? (
          <>
            {Object.entries(stats.by_type).map(([type, count]) => (
              <Tag key={type} label={`${type}: ${count}`} color={actionColor(type)} size="sm" />
            ))}
            {stats.total_resolved > 0 && (
              <span className="text-[10px] text-text-quaternary ml-1 tabular-nums">{stats.total_resolved} resolved</span>
            )}
          </>
        ) : null}
        <div className="ml-auto">
          <button
            onClick={() => runScan.mutate()}
            disabled={runScan.isPending}
            className="flex items-center gap-1.5 h-8 px-3.5 rounded-full bg-[rgba(30,26,22,0.60)] backdrop-blur-sm border border-[rgba(255,245,235,0.06)] shadow-[0_2px_12px_rgba(0,0,0,0.3)] text-[11px] font-[510] text-text-tertiary hover:text-text-secondary transition-colors cursor-pointer disabled:opacity-40"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            {runScan.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
            Run scan
          </button>
        </div>
      </div>

      {/* Issue list */}
      {queueLoading ? (
        <SkeletonRows count={4} />
      ) : issues.length === 0 ? (
        <EmptyState
          icon={<ShieldCheck />}
          title="All clean"
          description="No hygiene issues found. Run a scan to check for duplicates and data quality issues."
        />
      ) : (
        <div className="space-y-2">
          {issues.map(issue => (
            <IssueRow key={issue.id} issue={issue} />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// HygieneBadge — small indicator for the tab bar area
// ---------------------------------------------------------------------------

export function HygieneBadge({ onClick }: { onClick: () => void }) {
  const { data } = useHygieneStats();
  const count = data?.total_pending ?? 0;

  if (count === 0) return null;

  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1.5 h-8 px-3 rounded-full bg-[rgba(30,26,22,0.60)] backdrop-blur-xl border border-[rgba(255,245,235,0.06)] shadow-[0_2px_12px_rgba(0,0,0,0.3)] text-[11px] font-[510] text-accent hover:text-accent-hover transition-colors cursor-pointer"
      style={{ transitionDuration: 'var(--duration-instant)' }}
      title="Data hygiene issues pending"
    >
      <ShieldCheck className="w-3 h-3" />
      <span className="tabular-nums">{count}</span>
    </button>
  );
}
