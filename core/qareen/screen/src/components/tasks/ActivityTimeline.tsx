/**
 * ActivityTimeline — the card's story (Kanban Phase 2, the narrative layer).
 *
 * Renders a task's task_activity log newest-first: actor avatars (operator vs
 * purple agent vs system-gray), a kind icon per event, and expandable data
 * payloads for the rich kinds (attempt / proof carry branch, commits, test
 * results). This is what makes a delegated task legible after the fact —
 * "created → triaged → delegated → attempt → proof → in review" reads as one
 * coherent story, nothing flattened.
 */

import { useState } from 'react';
import {
  Plus, GitBranch, UserCheck, Undo2, MessageSquare, Hammer,
  BadgeCheck, Ban, Play, Pencil, Link2, ChevronRight, Circle,
} from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { useTaskActivity, useAppendActivity, type ActivityEntry } from '@/hooks/useActivity';

const AGENT_HUE = '#BF5AF2';

// kind → { icon, hue }. Hue tints the node dot so the eye can skim the story.
const KIND: Record<string, { icon: typeof Plus; hue: string; label: string }> = {
  created:        { icon: Plus,          hue: '#6B6560', label: 'Created' },
  status_changed: { icon: GitBranch,     hue: '#0A84FF', label: 'Status' },
  delegated:      { icon: UserCheck,     hue: AGENT_HUE, label: 'Delegated' },
  held:           { icon: Undo2,         hue: '#FF9F0A', label: 'Held' },
  comment:        { icon: MessageSquare, hue: '#6B6560', label: 'Comment' },
  attempt:        { icon: Hammer,        hue: '#0A84FF', label: 'Attempt' },
  proof:          { icon: BadgeCheck,    hue: '#30D158', label: 'Proof' },
  blocked:        { icon: Ban,           hue: '#FF453A', label: 'Blocked' },
  unblocked:      { icon: Play,          hue: '#30D158', label: 'Unblocked' },
  edited:         { icon: Pencil,        hue: '#6B6560', label: 'Edited' },
  linked:         { icon: Link2,         hue: '#6B6560', label: 'Linked' },
};

function relTime(ts: string): string {
  try {
    return formatDistanceToNow(new Date(ts), { addSuffix: true });
  } catch {
    return '';
  }
}

/** Small actor chip: operator (accent), agent (purple), system (gray). */
function ActorTag({ actor, actorType }: { actor: string; actorType: string }) {
  const name = actor.startsWith('agent:') ? actor.slice('agent:'.length)
    : actor.startsWith('system:') ? actor.slice('system:'.length)
    : actor;
  const hue = actorType === 'agent' ? AGENT_HUE : actorType === 'system' ? '#6B6560' : '#0A84FF';
  return (
    <span className="inline-flex items-center gap-1 text-[11px] font-[560]" style={{ color: hue }}>
      <span className="w-[5px] h-[5px] rounded-full" style={{ backgroundColor: hue }} />
      {actorType === 'operator' ? 'You' : name}
    </span>
  );
}

/** The expandable structured payload for attempt/proof/etc. */
function DataPayload({ data }: { data: Record<string, unknown> }) {
  const keys = Object.keys(data);
  if (keys.length === 0) return null;
  return (
    <div className="mt-1.5 rounded-md bg-bg-tertiary border border-border-secondary p-2 space-y-1">
      {keys.map((k) => {
        const v = data[k];
        const text = Array.isArray(v) ? v.map(String).join(', ')
          : typeof v === 'object' && v !== null ? JSON.stringify(v)
          : String(v);
        return (
          <div key={k} className="flex gap-2 text-[12px] leading-[1.5]">
            <span className="text-text-quaternary shrink-0 font-mono">{k}</span>
            <span className="text-text-tertiary font-mono break-all">{text}</span>
          </div>
        );
      })}
    </div>
  );
}

function Entry({ entry }: { entry: ActivityEntry }) {
  const meta = KIND[entry.kind] ?? { icon: Circle, hue: '#6B6560', label: entry.kind };
  const Icon = meta.icon;
  const hasData = entry.data && Object.keys(entry.data).length > 0;
  const richKind = entry.kind === 'attempt' || entry.kind === 'proof' || entry.kind === 'blocked';
  const [open, setOpen] = useState(richKind);

  return (
    <div className="flex gap-3">
      {/* Rail node */}
      <div className="flex flex-col items-center shrink-0">
        <span
          className="w-6 h-6 rounded-full flex items-center justify-center"
          style={{ backgroundColor: `${meta.hue}1f`, color: meta.hue }}
        >
          <Icon className="w-3 h-3" />
        </span>
        <span className="flex-1 w-px bg-border mt-1" />
      </div>

      {/* Body */}
      <div className="flex-1 min-w-0 pb-4">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[14px] text-text-secondary leading-[1.4]">{entry.body}</span>
          {hasData && (
            <button
              onClick={() => setOpen((o) => !o)}
              className="text-text-quaternary hover:text-text-tertiary transition-colors"
              aria-label={open ? 'Hide details' : 'Show details'}
              style={{ transitionDuration: 'var(--duration-instant)' }}
            >
              <ChevronRight className={`w-3.5 h-3.5 transition-transform ${open ? 'rotate-90' : ''}`} />
            </button>
          )}
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          <ActorTag actor={entry.actor} actorType={entry.actor_type} />
          <span className="text-[11px] text-text-quaternary">·</span>
          <span className="text-[11px] text-text-quaternary">{relTime(entry.timestamp)}</span>
        </div>
        {hasData && open && <DataPayload data={entry.data!} />}
      </div>
    </div>
  );
}

export function ActivityTimeline({ taskId }: { taskId: string }) {
  const { data: entries = [], isLoading } = useTaskActivity(taskId);
  const append = useAppendActivity(taskId);
  const [comment, setComment] = useState('');

  const ordered = [...entries].reverse(); // newest-first

  const send = () => {
    const body = comment.trim();
    if (!body) return;
    append.mutate({ kind: 'comment', body });
    setComment('');
  };

  return (
    <div className="mb-2">
      <span className="text-[12px] font-[590] text-text-quaternary uppercase tracking-wider">Activity</span>

      {/* Comment composer */}
      <div className="flex items-center gap-2 mt-2 mb-4">
        <input
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') send(); }}
          placeholder="Add a comment…"
          className="flex-1 text-[13px] bg-bg-secondary rounded-lg border border-border px-3 py-1.5 text-text-secondary outline-none placeholder:text-text-quaternary focus:border-border-tertiary"
        />
        <button
          onClick={send}
          disabled={!comment.trim() || append.isPending}
          className="text-[13px] text-accent hover:text-accent-hover disabled:text-text-quaternary disabled:opacity-40 cursor-pointer transition-colors px-2"
          style={{ transitionDuration: 'var(--duration-instant)' }}
        >
          Comment
        </button>
      </div>

      {isLoading ? (
        <p className="text-[13px] text-text-quaternary opacity-50 py-2">Loading…</p>
      ) : ordered.length === 0 ? (
        <p className="text-[13px] text-text-quaternary opacity-40 py-2">No activity yet.</p>
      ) : (
        <div>
          {ordered.map((e, i) => (
            <Entry key={e.id ?? `${e.timestamp}-${i}`} entry={e} />
          ))}
        </div>
      )}
    </div>
  );
}
