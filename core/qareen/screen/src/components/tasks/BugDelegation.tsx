/**
 * Shared Kanban Phase 1 task chrome — delegation control + bug-class badges.
 *
 * Used by both the board card (Tasks.tsx) and the task overlay (TaskOverlay.tsx)
 * so the two surfaces render delegation and bug richness identically.
 */

import { useState } from 'react';
import { User } from 'lucide-react';
import { useDelegate, heldAgent, type Task } from '@/hooks/useWork';
import { useAgents } from '@/hooks/useAgents';

// Agent hue — the system's "held by an agent" convention (Phase 0 inbox edge).
export const AGENT_HUE = '#BF5AF2';
// Bug severity → dot color. 1 = crash/highest, 4 = trivial.
export const SEV_COLOR: Record<number, string> = { 1: '#FF453A', 2: '#FF9F0A', 3: '#FFD60A', 4: '#6B6560' };
// Category color for the coarse board status a card sits in.
export const STAT_COLOR: Record<string, string> = {
  triage: '#BF5AF2', backlog: '#6B6560', todo: '#6B6560', active: '#0A84FF',
  waiting: '#FFD60A', in_review: '#BF5AF2', done: '#30D158', cancelled: '#4A4540',
};

/** Fine-stage label for a bug card, e.g. 'awaiting-approval' → 'Awaiting Approval'. */
export function stageLabel(stage?: string | null): string {
  if (!stage) return '';
  return stage.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

/** Inline agent picker that delegates a task (or takes it back). Just the state
 *  change + task.delegated event — no runner (Phase 4-5). */
export function DelegateControl({ task, compact = false }: { task: Task; compact?: boolean }) {
  const { delegate, hold } = useDelegate();
  const { data: agents = [] } = useAgents();
  const [open, setOpen] = useState(false);
  const held = heldAgent(task);

  return (
    <div className="relative" data-popover onClick={e => e.stopPropagation()}>
      <button
        onClick={() => setOpen(o => !o)}
        title={held ? `Held by agent:${held}` : 'Delegate to an agent'}
        className={`flex items-center gap-1 rounded-full transition-colors duration-75 ${
          held
            ? 'px-1.5 py-[1px] text-[11px] font-[560]'
            : 'px-1.5 py-[1px] text-[11px] text-text-quaternary hover:text-text-tertiary border border-border-secondary hover:border-border-tertiary'
        }`}
        style={held ? { color: AGENT_HUE, backgroundColor: 'rgba(191,90,242,0.12)' } : undefined}
      >
        {held ? (
          <>
            <span className="w-[6px] h-[6px] rounded-full" style={{ backgroundColor: AGENT_HUE }} />
            {held}
          </>
        ) : (
          <>{!compact && <User className="w-3 h-3" />}Delegate</>
        )}
      </button>
      {open && (
        <div className="absolute z-30 top-full right-0 mt-1 w-44 rounded-lg bg-bg-panel border border-border shadow-[0_8px_24px_rgba(0,0,0,0.4)] py-1"
          data-popover>
          {held && (
            <button
              onClick={() => { hold.mutate(task.id); setOpen(false); }}
              className="w-full text-left px-3 py-1.5 text-[13px] text-text-tertiary hover:bg-bg-tertiary">
              Take back (operator)
            </button>
          )}
          <div className="px-3 pt-1 pb-0.5 text-[10px] uppercase tracking-wider text-text-quaternary">Delegate to</div>
          {agents.filter(a => a.is_active).map(a => (
            <button key={a.id}
              onClick={() => { delegate.mutate({ id: task.id, agent: a.name.toLowerCase() }); setOpen(false); }}
              className="w-full flex items-center gap-2 text-left px-3 py-1.5 text-[13px] text-text-secondary hover:bg-bg-tertiary">
              <span className="w-[6px] h-[6px] rounded-full" style={{ backgroundColor: a.color || AGENT_HUE }} />
              {a.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Badges shown on a bug-class card: app id + a severity dot. */
export function BugBadges({ task }: { task: Task }) {
  const f = task.fields ?? {};
  if (task.pipeline !== 'bug') return null;
  const sev = typeof f.severity === 'number' ? f.severity : undefined;
  return (
    <span className="inline-flex items-center gap-1.5">
      {f.app && (
        <span className="px-1.5 py-[1px] rounded text-[10px] font-[560] uppercase tracking-wide text-text-tertiary bg-bg-tertiary border border-border-secondary">
          {String(f.app)}
        </span>
      )}
      {sev !== undefined && (
        <span className="w-[7px] h-[7px] rounded-full" title={`Severity ${sev}`}
          style={{ backgroundColor: SEV_COLOR[sev] ?? '#6B6560' }} />
      )}
    </span>
  );
}

/** Full bug-class richness block for the detail surfaces — kept unflattened. */
export function BugDetails({ task }: { task: Task }) {
  if (task.pipeline !== 'bug') return null;
  const f = task.fields ?? {};
  const refs = Array.isArray(f.code_refs) ? f.code_refs : [];
  return (
    <div className="mb-5 p-3 rounded-lg bg-bg-secondary border border-border">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[12px] font-[590] uppercase tracking-wider" style={{ color: STAT_COLOR[task.status] ?? AGENT_HUE }}>
          Bug{task.stage ? ` · ${stageLabel(task.stage)}` : ''}
        </span>
        <BugBadges task={task} />
      </div>
      <div className="space-y-2 text-[14px]">
        {f.root_cause && <p className="text-text-secondary leading-[1.6]"><span className="text-text-quaternary">Root cause </span>{String(f.root_cause)}</p>}
        {f.fix_approach && <p className="text-text-tertiary leading-[1.6]"><span className="text-text-quaternary">Fix </span>{String(f.fix_approach)}</p>}
        {refs.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {refs.map((r, i) => (
              <code key={i} className="px-1.5 py-[1px] rounded text-[11px] font-mono text-text-tertiary bg-bg-tertiary">{String(r)}</code>
            ))}
          </div>
        )}
        {f.branch && <p className="text-[12px] font-mono text-text-quaternary">{String(f.branch)}</p>}
      </div>
    </div>
  );
}
