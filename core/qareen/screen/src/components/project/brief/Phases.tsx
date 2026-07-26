/**
 * Tasks, grouped by PHASE rather than by status.
 *
 * Status groups tell you what shape the work is in. Phases tell you where the
 * project is — which is the question the operator is actually asking. Each
 * phase carries its own progress so a stalled middle phase is visible without
 * counting rows.
 *
 * Any task the compiler didn't place in a phase still renders, under "Other
 * work" — a task must never disappear because the grouping missed it.
 */

import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { Phase } from '@/hooks/useProjectBrief';
import type { Task } from '@/hooks/useWork';
import type { AreaTone } from '@/lib/areaStyle';
import { phaseStateLabel, phaseStateTone } from './briefStyle';
import { KindChip, Overline, ProgressRail } from './chrome';
import { TaskRow } from './TaskRow';

interface Group {
  key: string;
  label: string;
  state: string;
  done: number;
  total: number;
  tasks: Task[];
}

function GroupCard({
  group,
  tone,
  defaultOpen,
  onToggleTask,
}: {
  group: Group;
  tone: AreaTone;
  defaultOpen: boolean;
  onToggleTask: (t: Task) => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const stateTone = phaseStateTone(group.state);

  return (
    <div className="bg-bg-secondary/50 border border-border rounded-[7px] px-3 py-2.5">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2.5 cursor-pointer group"
      >
        {open
          ? <ChevronDown className="w-3.5 h-3.5 text-text-quaternary shrink-0" />
          : <ChevronRight className="w-3.5 h-3.5 text-text-quaternary shrink-0" />}
        <span className="text-[14px] font-[510] text-text truncate text-left flex-1 min-w-0">
          {group.label}
        </span>
        <KindChip label={phaseStateLabel(group.state)} tone={stateTone} />
        <span className="w-[64px] hidden sm:flex items-center shrink-0">
          <ProgressRail done={group.done} total={group.total} barClass={tone.dot} />
        </span>
        <span className="text-[11px] font-mono text-text-quaternary tabular-nums shrink-0 w-[44px] text-right">
          {group.done}/{group.total}
        </span>
      </button>

      {open && group.tasks.length > 0 && (
        <div className="mt-1.5">
          {group.tasks.map(t => (
            <TaskRow key={t.id} task={t} dot={tone.dot} onToggle={() => onToggleTask(t)} />
          ))}
        </div>
      )}
      {open && group.tasks.length === 0 && (
        <p className="text-[13px] text-text-quaternary px-2 py-2">
          {group.total > 0
            ? 'These tasks are outside the loaded page.'
            : 'No tasks placed in this phase yet.'}
        </p>
      )}
    </div>
  );
}

export function Phases({
  phases,
  tasks,
  tone,
  onToggleTask,
}: {
  phases: Phase[];
  tasks: Task[];
  tone: AreaTone;
  onToggleTask: (t: Task) => void;
}) {
  const byId = new Map(tasks.map(t => [t.id, t]));
  const placed = new Set<string>();

  const groups: Group[] = phases.map(p => {
    const rows: Task[] = [];
    for (const id of p.task_ids) {
      const t = byId.get(id);
      if (t) { rows.push(t); placed.add(id); }
    }
    return { key: p.key, label: p.label, state: p.state, done: p.done, total: p.total, tasks: rows };
  });

  const leftovers = tasks.filter(t => !placed.has(t.id));
  if (leftovers.length > 0) {
    const done = leftovers.filter(t => t.status === 'done').length;
    groups.push({
      key: '__unphased',
      label: 'Other work',
      state: done === leftovers.length ? 'done' : 'in_progress',
      done,
      total: leftovers.length,
      tasks: leftovers,
    });
  }

  if (groups.length === 0) return null;

  return (
    <section className="mb-10">
      <Overline count={groups.length}>Phases</Overline>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-x-5 gap-y-2.5 items-start">
        {groups.map(g => (
          <GroupCard
            key={g.key}
            group={g}
            tone={tone}
            defaultOpen={g.state !== 'done'}
            onToggleTask={onToggleTask}
          />
        ))}
      </div>
    </section>
  );
}

// ── Fallback: status grouping ───────────────────────────────────────────────

const STATUS_GROUPS: { status: Task['status']; label: string }[] = [
  { status: 'active', label: 'Active' },
  { status: 'todo', label: 'Todo' },
  { status: 'waiting', label: 'Waiting' },
  { status: 'done', label: 'Done' },
];

/**
 * The pre-brief rendering, kept intact. Used when the compiler produced no
 * phases, and when the brief endpoint isn't available at all — a project page
 * degrades to this rather than to a broken page.
 */
export function StatusGroups({
  tasks,
  tone,
  onToggleTask,
}: {
  tasks: Task[];
  tone: AreaTone;
  onToggleTask: (t: Task) => void;
}) {
  const [showDone, setShowDone] = useState(false);

  return (
    <>
      {STATUS_GROUPS.map(({ status, label }) => {
        const items = tasks.filter(t => t.status === status);
        if (items.length === 0) return null;
        const isDoneGroup = status === 'done';
        const open = !isDoneGroup || showDone;
        return (
          <div key={status} className="mb-6">
            {isDoneGroup ? (
              <button onClick={() => setShowDone(!showDone)} className="flex items-center gap-2 px-1 mb-1.5 cursor-pointer">
                {showDone
                  ? <ChevronDown className="w-3 h-3 text-text-quaternary" />
                  : <ChevronRight className="w-3 h-3 text-text-quaternary" />}
                <span className="text-[12px] font-[590] uppercase tracking-[0.06em] text-text-tertiary">{label}</span>
                <span className="text-[12px] font-mono text-text-quaternary">{items.length}</span>
              </button>
            ) : (
              <div className="flex items-center gap-2 px-1 mb-1.5">
                <span className="text-[12px] font-[590] uppercase tracking-[0.06em] text-text-tertiary">{label}</span>
                <span className="text-[12px] font-mono text-text-quaternary">{items.length}</span>
              </div>
            )}
            {open && items.map(t => (
              <TaskRow key={t.id} task={t} dot={tone.dot} onToggle={() => onToggleTask(t)} />
            ))}
          </div>
        );
      })}
    </>
  );
}
