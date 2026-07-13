/**
 * ShipLedger — Zone 2B, the ship walkthrough.
 *
 * The 14-batch ledger that replaces today's chat-driven triage. Each BatchRow:
 *   batch number (tinted to its graph color) + name + commit count (mono)
 *   + a STATUS pill (built/half-baked/broken)
 *   + a 3-way DECISION segmented control [ship · defer · hold], seeded from the
 *     triage rec, operator-overridable, persisted via POST decision (re-tints).
 * Expanding reveals the batch's commits (cross-highlighting the graph later), the
 * spec rationale (MarkdownRenderer), and watch-items as warning chips.
 *
 * A plan-level GATES strip surfaces the ship-map blockers (tsc / ship-check /
 * migration-safety). Gate EXECUTION is deferred — v1 renders the seeded state.
 */

import { useMemo, useState, type ReactNode } from 'react';
import {
  ChevronDown,
  ChevronRight,
  AlertTriangle,
  Check,
  X as XIcon,
  Circle,
  Play,
} from 'lucide-react';
import { MarkdownRenderer } from '@/components/primitives/MarkdownRenderer';
import {
  type Batch,
  type BatchDecision,
  type BatchStatus,
  type BatchesResponse,
  type GitCommit,
  type Gate,
  batchToneKey,
} from '@/lib/gitApi';
import { useRunGates, useSetBatchDecision } from '@/hooks/useGit';

// Full static class strings (Tailwind JIT requires complete strings).
const TONE: Record<string, { text: string; bg: string; dot: string }> = {
  green: { text: 'text-tag-green', bg: 'bg-tag-green-bg', dot: 'bg-tag-green' },
  purple: { text: 'text-tag-purple', bg: 'bg-tag-purple-bg', dot: 'bg-tag-purple' },
  blue: { text: 'text-tag-blue', bg: 'bg-tag-blue-bg', dot: 'bg-tag-blue' },
  orange: { text: 'text-tag-orange', bg: 'bg-tag-orange-bg', dot: 'bg-tag-orange' },
  teal: { text: 'text-tag-teal', bg: 'bg-tag-teal-bg', dot: 'bg-tag-teal' },
  pink: { text: 'text-tag-pink', bg: 'bg-tag-pink-bg', dot: 'bg-tag-pink' },
  gray: { text: 'text-tag-gray', bg: 'bg-tag-gray-bg', dot: 'bg-tag-gray' },
};

const STATUS_PILL: Record<BatchStatus, { label: string; text: string; bg: string }> = {
  built: { label: 'built', text: 'text-tag-green', bg: 'bg-tag-green-bg' },
  'half-baked': { label: 'half-baked', text: 'text-tag-orange', bg: 'bg-tag-orange-bg' },
  broken: { label: 'broken', text: 'text-tag-red', bg: 'bg-tag-red-bg' },
  unknown: { label: 'unverified', text: 'text-tag-gray', bg: 'bg-tag-gray-bg' },
};

const DECISIONS: BatchDecision[] = ['ship', 'defer', 'hold'];
const DECISION_ACTIVE: Record<BatchDecision, string> = {
  ship: 'bg-tag-green-bg text-tag-green',
  defer: 'bg-tag-gray-bg text-tag-gray',
  hold: 'bg-tag-orange-bg text-tag-orange',
  undecided: '',
};

function GateChip({ gate }: { gate: Gate }) {
  const map: Record<string, { text: string; bg: string; icon: ReactNode }> = {
    pass: { text: 'text-tag-green', bg: 'bg-tag-green-bg', icon: <Check className="w-3 h-3" /> },
    warn: { text: 'text-tag-orange', bg: 'bg-tag-orange-bg', icon: <AlertTriangle className="w-3 h-3" /> },
    fail: { text: 'text-tag-red', bg: 'bg-tag-red-bg', icon: <XIcon className="w-3 h-3" /> },
    running: { text: 'text-tag-blue', bg: 'bg-tag-blue-bg', icon: <Circle className="w-3 h-3 animate-pulse" /> },
    unknown: { text: 'text-text-quaternary', bg: 'bg-bg-tertiary', icon: <Circle className="w-3 h-3" /> },
  };
  const v = map[gate.status] ?? map.unknown;
  return (
    <div
      className={`flex items-center gap-1.5 h-7 px-2.5 rounded-full text-[12px] ${v.text} ${v.bg}`}
      title={
        gate.stale
          ? `${gate.summary || gate.id} — ran against an older commit; re-run`
          : gate.summary || gate.id
      }
      style={gate.stale ? { opacity: 0.45 } : undefined}
    >
      {v.icon}
      <span className="font-mono">{gate.id}</span>
      {gate.stale && (
        <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">
          stale
        </span>
      )}
      {gate.summary && (
        <span className="text-text-quaternary truncate max-w-[160px]">· {gate.summary}</span>
      )}
    </div>
  );
}

function BatchRow({
  batch,
  projectId,
  commitMap,
  subjects,
  selected,
  onSelect,
}: {
  batch: Batch;
  projectId: string;
  commitMap: Map<string, GitCommit>;
  subjects: Record<string, string>;
  selected: boolean;
  onSelect: (id: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const setDecision = useSetBatchDecision(projectId);

  const tone = TONE[batchToneKey(batch.ordinal)] ?? TONE.gray;
  const statusPill = STATUS_PILL[batch.status] ?? STATUS_PILL.unknown;
  // Prefer the reconciled live count; the spec count can drift after merges.
  const count = batch.commit_count_live ?? batch.commit_count;
  // Show the operator's decision once made, else the spec's suggestion.
  const effective: BatchDecision =
    batch.decision !== 'undecided' ? batch.decision : batch.suggested_decision;
  const decided = batch.decision !== 'undecided';
  const dimmed = effective === 'defer' || effective === 'hold';

  const choose = (d: BatchDecision) => {
    if (d === batch.decision) return;
    setDecision.mutate({ batchId: batch.id, body: { decision: d } });
  };

  return (
    <div
      className={`rounded-xl border transition-colors ${
        selected ? 'border-border-tertiary bg-bg-secondary' : 'border-border bg-bg-panel'
      }`}
      style={{ transitionDuration: 'var(--duration-fast)', opacity: dimmed && !open ? 0.74 : 1 }}
      onMouseEnter={() => onSelect(batch.id)}
      onMouseLeave={() => onSelect(null)}
    >
      {/* Header row */}
      <div className="flex items-center gap-3 px-3 h-14">
        <button
          onClick={() => setOpen((o) => !o)}
          className="shrink-0 flex items-center justify-center w-6 h-6 rounded-md text-text-quaternary hover:text-text-tertiary cursor-pointer"
          aria-label={open ? 'Collapse batch' : 'Expand batch'}
        >
          {open ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </button>

        {/* Tinted ordinal — matches the commit-graph node color */}
        <span
          className={`shrink-0 flex items-center justify-center w-6 h-6 rounded-full text-[12px] font-mono font-[600] ${tone.text} ${tone.bg}`}
        >
          {batch.ordinal}
        </span>

        <button
          onClick={() => setOpen((o) => !o)}
          className="flex-1 min-w-0 text-left cursor-pointer"
        >
          <span className="text-[14px] text-text-secondary truncate block">{batch.title}</span>
        </button>

        <span className="shrink-0 text-[12px] font-mono text-text-quaternary tabular-nums">
          {count} {count === 1 ? 'commit' : 'commits'}
        </span>

        {/* STATUS pill */}
        <span
          className={`shrink-0 hidden sm:inline-flex items-center h-6 px-2 rounded-full text-[11px] font-[510] ${statusPill.text} ${statusPill.bg}`}
        >
          {statusPill.label}
        </span>

        {/* DECISION segmented control */}
        <div className="shrink-0 flex items-center gap-0.5 p-0.5 rounded-full bg-bg-tertiary">
          {DECISIONS.map((d) => {
            const active = effective === d;
            return (
              <button
                key={d}
                onClick={() => choose(d)}
                className={`h-6 px-2.5 rounded-full text-[11px] font-[510] cursor-pointer transition-colors ${
                  active ? DECISION_ACTIVE[d] : 'text-text-quaternary hover:text-text-tertiary'
                } ${active && !decided ? 'ring-1 ring-inset ring-border-secondary' : ''}`}
                style={{ transitionDuration: 'var(--duration-instant)' }}
                title={active && !decided ? 'suggested by triage — click to confirm' : `mark ${d}`}
              >
                {d}
              </button>
            );
          })}
        </div>
      </div>

      {/* Expanded body */}
      {open && (
        <div className="px-4 pb-4 pt-1 border-t border-border space-y-3">
          {/* Watch-items */}
          {batch.watch_items.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {batch.watch_items.map((w, i) => (
                <span
                  key={i}
                  className="inline-flex items-center gap-1.5 max-w-full px-2 py-1 rounded-md text-[11px] text-tag-orange bg-tag-orange-bg"
                >
                  <AlertTriangle className="w-3 h-3 shrink-0" />
                  <span className="truncate">{w}</span>
                </span>
              ))}
            </div>
          )}

          {/* Rationale (the only place serif is even optional) */}
          {batch.rationale && (
            <div className="text-[13px]">
              <MarkdownRenderer content={batch.rationale} compact />
            </div>
          )}

          {/* Commits in this batch */}
          <div className="space-y-0.5">
            <div className="text-[11px] font-[590] uppercase tracking-[0.06em] text-text-tertiary mb-1">
              commits · {batch.assignment}
            </div>
            {batch.commits.map((sha) => {
              const subject = subjects[sha] ?? commitMap.get(sha)?.subject;
              return (
                <div key={sha} className="flex items-center gap-2.5 h-7 px-1 rounded-md hover:bg-bg-secondary">
                  <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${tone.dot}`} />
                  <span className="font-mono text-[11px] text-text-quaternary shrink-0">{sha.slice(0, 7)}</span>
                  <span className="text-[12px] text-text-tertiary truncate">
                    {subject ?? <span className="text-text-quaternary italic">already merged or unreachable</span>}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export default function ShipLedger({
  plan,
  commits,
  projectId,
  selectedBatchId,
  onSelectBatch,
}: {
  plan: BatchesResponse;
  commits: GitCommit[];
  projectId: string;
  selectedBatchId: string | null;
  onSelectBatch: (id: string | null) => void;
}) {
  const commitMap = useMemo(
    () => new Map(commits.map((c) => [c.sha, c])),
    [commits],
  );
  const gates = plan.gates ? Object.values(plan.gates) : [];
  const runGates = useRunGates(projectId);
  const gatesRunning = gates.some((g) => g.status === 'running') || runGates.isPending;

  const decidedShip = plan.batches.filter((b) => b.decision === 'ship').length;
  const suggestShip = plan.batches.filter(
    (b) => b.decision === 'undecided' && b.suggested_decision === 'ship',
  ).length;

  return (
    <div>
      {/* Ledger header */}
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-[13px] font-[600] uppercase tracking-[0.06em] text-text-tertiary">
          Ship walkthrough
        </h3>
        <span className="text-[12px] font-mono text-text-quaternary tabular-nums">
          {decidedShip > 0 ? `${decidedShip} cleared · ` : ''}
          {suggestShip > 0 ? `${suggestShip} suggested ship · ` : ''}
          {plan.batches.length} batches
        </span>
      </div>

      {/* Plan-level gates + the Run button (background run streams results live) */}
      <div className="mb-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[10px] font-[590] uppercase tracking-[0.08em] text-text-quaternary">
            Gates
          </span>
          <button
            onClick={() => runGates.mutate()}
            disabled={gatesRunning}
            className="flex items-center gap-1.5 h-7 px-3 rounded-full text-[12px] text-accent border border-accent/40 hover:bg-accent-subtle cursor-pointer disabled:opacity-50"
            style={{ transitionDuration: 'var(--duration-fast)' }}
          >
            <Play className={`w-3 h-3 ${gatesRunning ? 'animate-pulse' : ''}`} />
            {gatesRunning ? 'Running…' : 'Run gates'}
          </button>
        </div>
        {gates.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {gates.map((g) => (
              <GateChip key={g.id} gate={g} />
            ))}
          </div>
        )}
      </div>

      {/* Drift / overflow banner */}
      {(plan.drift || plan.overflow) && (
        <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-lg text-[12px] text-tag-orange bg-tag-orange-bg">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          {plan.overflow
            ? 'Some commits fell outside the spec batch counts — see the uncategorized batch.'
            : 'Plan drifted from the live unmerged set — some pinned commits already merged.'}
        </div>
      )}

      {/* Batch rows */}
      <div className="space-y-2">
        {plan.batches.map((b) => (
          <BatchRow
            key={b.id}
            batch={b}
            projectId={projectId}
            commitMap={commitMap}
            subjects={plan.subjects ?? {}}
            selected={selectedBatchId === b.id}
            onSelect={onSelectBatch}
          />
        ))}
      </div>

      {plan.batches.length === 0 && (
        <p className="text-[14px] text-text-quaternary py-6 text-center">
          No unmerged batches — this branch has nothing to ship.
        </p>
      )}
    </div>
  );
}
