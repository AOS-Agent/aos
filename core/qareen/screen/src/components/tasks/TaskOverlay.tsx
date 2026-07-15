/**
 * TaskOverlay — Notion-style task peek.
 *
 * A centered modal floating over a dimmed+blurred backdrop. Opened by any row in
 * the Work section via `useTaskOverlay().openTask(id)`. Reuses the field/edit
 * logic from Tasks.tsx's DetailPanel, reframed as a centered reading surface.
 *
 * Closes on X, Esc, or backdrop click — and ANIMATES both in and out (DESIGN
 * rule 7) via an internal `closing` flag + onAnimationEnd, mirroring ProjectDetail.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { X, User, Calendar } from 'lucide-react';
import { useWork, type Task, type Project, type Goal } from '@/hooks/useWork';
import { useUpdateTask, useCreateTask } from '@/hooks/useTasks';
import { areaTone, taskGoalId } from '@/lib/areaStyle';
import { Tag } from '@/components/primitives/Tag';
import { TaskStatus, TaskPriority } from '@/lib/types';
import { format, isPast, isToday, isTomorrow, differenceInDays } from 'date-fns';

// ── Shared visual maps (kept in sync with the rest of the task surface) ──────
const STAT_COLOR: Record<string, string> = { todo: '#6B6560', active: '#0A84FF', waiting: '#FFD60A', done: '#30D158', cancelled: '#4A4540' };

function formatDue(iso: string): { text: string; overdue: boolean } {
  const d = new Date(iso);
  if (isToday(d)) return { text: 'Today', overdue: false };
  if (isTomorrow(d)) return { text: 'Tomorrow', overdue: false };
  if (isPast(d)) return { text: `${Math.abs(differenceInDays(d, new Date()))}d overdue`, overdue: true };
  const days = differenceInDays(d, new Date());
  return days <= 7 ? { text: `in ${days}d`, overdue: false } : { text: format(d, 'MMM d'), overdue: false };
}

function findTask(list: Task[] | undefined, id: string): Task | undefined {
  if (!list) return undefined;
  for (const t of list) {
    if (t.id === id) return t;
    const found = findTask(t.subtasks, id);
    if (found) return found;
  }
  return undefined;
}

// ═══════════════════════════════════════════════════════════════════════════
// TaskOverlay
// ═══════════════════════════════════════════════════════════════════════════

export function TaskOverlay({ taskId, onClose }: { taskId: string; onClose: () => void }) {
  const { data } = useWork();
  const projects = (data?.projects ?? []) as Project[];
  const goals = (data?.goals ?? []) as Goal[];

  const fromWork = findTask(data?.tasks, taskId);

  // Fallback fetch — tasks beyond the global /api/work cap (e.g. large projects
  // opened from ProjectDetail) aren't in the work payload; fetch them directly.
  const { data: fetched } = useQuery({
    queryKey: ['task', taskId],
    enabled: !fromWork,
    staleTime: 30_000,
    queryFn: async (): Promise<Task | null> => {
      const res = await fetch(`/api/tasks/${taskId}`);
      if (!res.ok) return null;
      return (await res.json()) as Task;
    },
  });

  const task = fromWork ?? fetched ?? null;

  // Close animation (rule 7) — owned internally so callers stay one-liners.
  const [closing, setClosing] = useState(false);
  const handleClose = useCallback(() => setClosing(true), []);

  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); handleClose(); } };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [handleClose]);

  const keyframes = (
    <style>{`
      @keyframes peekIn { from { opacity: 0; transform: translateY(8px) scale(0.97); } to { opacity: 1; transform: none; } }
      @keyframes peekOut { from { opacity: 1; transform: none; } to { opacity: 0; transform: translateY(8px) scale(0.97); } }
      @keyframes peekFadeIn { from { opacity: 0; } to { opacity: 1; } }
      @keyframes peekFadeOut { from { opacity: 1; } to { opacity: 0; } }
    `}</style>
  );

  const backdropAnim = closing ? 'animate-[peekFadeOut_160ms_ease-in]' : 'animate-[peekFadeIn_180ms_ease-out]';
  const modalAnim = closing ? 'animate-[peekOut_160ms_ease-in]' : 'animate-[peekIn_200ms_ease-out]';

  const due = task?.due ? formatDue(task.due) : null;
  const gid = task ? taskGoalId(task, projects) : null;
  const goalIdx = goals.findIndex(g => g.id === gid);
  const tone = areaTone(gid, goalIdx < 0 ? 0 : goalIdx);
  const projTitle = task?.project
    ? (projects.find(p => p.id === task.project || p.title === task.project)?.title ?? task.project)
    : null;

  return (
    <div className="fixed inset-0 flex items-center justify-center p-4" style={{ zIndex: 'var(--z-dialog)' }}>
      {/* Backdrop — dimmed + blurred, click to close */}
      <div
        onClick={handleClose}
        className={`absolute inset-0 cursor-pointer ${backdropAnim}`}
        style={{ background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(4px)' }}
      />

      {/* Peek card */}
      <div
        onAnimationEnd={() => { if (closing) onClose(); }}
        className={`relative w-full max-w-[680px] max-h-[85vh] overflow-y-auto bg-bg-panel border border-border-secondary rounded-[14px] shadow-[0_24px_80px_rgba(0,0,0,0.6)] ${modalAnim}`}
      >
        {/* Header — sticky so close stays reachable while scrolling */}
        <div className="sticky top-0 z-10 flex items-center justify-between px-6 h-12 bg-bg-panel border-b border-border">
          <span className="text-[12px] font-mono text-text-quaternary">{task?.id ?? taskId}</span>
          <button
            onClick={handleClose}
            aria-label="Close"
            className="w-7 h-7 rounded-lg flex items-center justify-center text-text-quaternary hover:text-text-tertiary hover:bg-bg-tertiary cursor-pointer transition-colors"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {!task ? (
          <div className="px-6 py-16 text-center">
            <p className="text-[15px] text-text-quaternary opacity-60">
              {fromWork === undefined && fetched === null ? 'Task not found.' : 'Loading task…'}
            </p>
          </div>
        ) : (
          <div className="px-6 py-5">
            {/* Title — inline editable */}
            <TitleEditor task={task} />

            {/* Properties */}
            <div className="space-y-3 mb-6">
              {/* Status */}
              <div className="flex items-center justify-between text-[14px]">
                <span className="text-text-quaternary">Status</span>
                <StatusControl task={task} />
              </div>

              {/* Priority */}
              <div className="flex items-center justify-between text-[14px]">
                <span className="text-text-quaternary">Priority</span>
                <PriorityControl task={task} />
              </div>

              {/* Project — area-color dot + resolved title */}
              {projTitle && (
                <div className="flex items-center justify-between text-[14px]">
                  <span className="text-text-quaternary">Project</span>
                  <span className="flex items-center gap-2">
                    <span className={`w-[7px] h-[7px] rounded-full shrink-0 ${tone.dot}`} />
                    <span className={`${tone.text}`}>{projTitle}</span>
                  </span>
                </div>
              )}

              {/* Assignee */}
              {task.assigned_to && (
                <div className="flex items-center justify-between text-[14px]">
                  <span className="text-text-quaternary">Assignee</span>
                  <span className="text-text-secondary flex items-center gap-1.5"><User className="w-3 h-3" />{task.assigned_to}</span>
                </div>
              )}

              {/* Due */}
              {due && (
                <div className="flex items-center justify-between text-[14px]">
                  <span className="text-text-quaternary">Due</span>
                  <span className={`flex items-center gap-1.5 ${due.overdue ? 'text-red' : 'text-text-secondary'}`}><Calendar className="w-3 h-3" />{due.text}</span>
                </div>
              )}
            </div>

            {/* Divider */}
            <div className="border-t border-border mb-5" />

            {/* Tags */}
            {task.tags?.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mb-5">{task.tags.map(t => <Tag key={t} label={t} color="gray" />)}</div>
            )}

            {/* Description — editable, on a serif reading surface */}
            <DescriptionEditor task={task} />

            {/* Subtasks */}
            <SubtaskSection task={task} />

            {/* Handoff */}
            {task.handoff && (
              <div className="p-3 rounded-lg bg-bg-secondary border border-border mb-5">
                <span className="text-[12px] font-[590] text-purple uppercase tracking-wider">Agent handoff</span>
                <div className="mt-2 space-y-2 text-[14px]">
                  {task.handoff.state && <p className="font-sans text-text-secondary leading-[1.65]">{task.handoff.state}</p>}
                  {task.handoff.next_step && <p className="text-text-tertiary"><span className="text-accent font-[510]">Next</span> {task.handoff.next_step}</p>}
                  {task.handoff.blockers?.length > 0 && <p className="text-red/70">Blocked: {task.handoff.blockers.join(', ')}</p>}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
      {keyframes}
    </div>
  );
}

// ── Title ────────────────────────────────────────────────────────────────────

function TitleEditor({ task }: { task: Task }) {
  const update = useUpdateTask();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(task.title);

  useEffect(() => { setDraft(task.title); setEditing(false); }, [task.id, task.title]);

  const save = () => {
    if (draft.trim() && draft !== task.title) update.mutate({ id: task.id, data: { title: draft.trim() } });
    setEditing(false);
  };

  if (editing) {
    return (
      <input value={draft} onChange={e => setDraft(e.target.value)} onBlur={save}
        onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') { setDraft(task.title); setEditing(false); } }}
        autoFocus className="w-full text-[24px] font-[600] text-text bg-transparent outline-none border-b border-accent pb-1 mb-6" />
    );
  }

  return (
    <h2 onClick={() => setEditing(true)}
      className="text-[24px] font-[600] text-text mb-6 cursor-text leading-[1.35] hover:text-accent transition-colors"
      style={{ transitionDuration: 'var(--duration-instant)' }}>
      {task.title}
    </h2>
  );
}

// ── Status ───────────────────────────────────────────────────────────────────

function StatusControl({ task }: { task: Task }) {
  const update = useUpdateTask();
  return (
    <div className="flex items-center gap-2">
      <div className="w-2 h-2 rounded-full" style={{ backgroundColor: STAT_COLOR[task.status] }} />
      <select value={task.status} onChange={e => update.mutate({ id: task.id, data: { status: e.target.value as TaskStatus } })}
        className="bg-transparent text-text-secondary outline-none cursor-pointer text-right appearance-none">
        <option value="todo">Todo</option><option value="active">Active</option><option value="waiting">Waiting</option>
        <option value="done">Done</option><option value="cancelled">Cancelled</option>
      </select>
    </div>
  );
}

// ── Priority ─────────────────────────────────────────────────────────────────

function PriorityControl({ task }: { task: Task }) {
  const update = useUpdateTask();
  return (
    <div className="flex gap-0.5">{[1, 2, 3, 4, 5].map(p =>
      <button key={p} onClick={() => update.mutate({ id: task.id, data: { priority: p as TaskPriority } })}
        className={`w-6 h-6 rounded-md text-[12px] font-[590] cursor-pointer transition-all ${
          task.priority === p ? 'text-text bg-bg-tertiary' : 'text-text-quaternary hover:text-text-tertiary hover:bg-bg-secondary'
        }`} style={{ transitionDuration: 'var(--duration-instant)' }}>{p}</button>
    )}</div>
  );
}

// ── Description ──────────────────────────────────────────────────────────────

function DescriptionEditor({ task }: { task: Task }) {
  const update = useUpdateTask();
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(task.description ?? '');
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { setVal(task.description ?? ''); }, [task.description]);
  useEffect(() => { if (editing && ref.current) { ref.current.focus(); ref.current.style.height = 'auto'; ref.current.style.height = ref.current.scrollHeight + 'px'; } }, [editing]);

  const save = () => {
    const trimmed = val.trim();
    if (trimmed !== (task.description ?? '')) update.mutate({ id: task.id, data: { description: trimmed || undefined } as never });
    setEditing(false);
  };

  if (editing) {
    return (
      <div className="mb-5">
        <span className="text-[12px] font-[590] text-text-quaternary uppercase tracking-wider">Description</span>
        <textarea ref={ref} value={val} onChange={e => { setVal(e.target.value); e.target.style.height = 'auto'; e.target.style.height = e.target.scrollHeight + 'px'; }}
          onBlur={save} onKeyDown={e => { if (e.key === 'Escape') { setVal(task.description ?? ''); setEditing(false); } }}
          className="w-full mt-2 p-3 font-sans text-[16px] text-text-secondary bg-bg-secondary rounded-lg border border-border outline-none resize-none leading-[1.7] focus:border-border-tertiary min-h-[72px]"
          placeholder="Add a description..." />
      </div>
    );
  }

  return (
    <div className="mb-5 cursor-text" onClick={() => setEditing(true)}>
      <span className="text-[12px] font-[590] text-text-quaternary uppercase tracking-wider">Description</span>
      {task.description ? (
        <p className="mt-2 font-sans text-[16px] text-text-secondary leading-[1.7] whitespace-pre-wrap hover:bg-bg-secondary rounded-lg p-2 -m-2 transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>{task.description}</p>
      ) : (
        <p className="mt-2 text-[14px] text-text-quaternary opacity-40 hover:opacity-60 transition-opacity p-1 -m-1">Click to add description...</p>
      )}
    </div>
  );
}

// ── Subtasks ─────────────────────────────────────────────────────────────────

function SubtaskSection({ task }: { task: Task }) {
  const update = useUpdateTask();
  const createTask = useCreateTask();
  const [adding, setAdding] = useState(false);
  const [newTitle, setNewTitle] = useState('');

  const subtasks = task.subtasks ?? [];
  const doneCount = subtasks.filter(s => s.status === 'done').length;

  const addSubtask = () => {
    if (!newTitle.trim()) return;
    createTask.mutate({ title: newTitle.trim(), parent_id: task.id } as never);
    setNewTitle('');
  };

  return (
    <div className="mb-5">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[12px] font-[590] text-text-quaternary uppercase tracking-wider">Subtasks</span>
        <div className="flex items-center gap-2">
          {subtasks.length > 0 && <span className="text-[12px] font-mono text-text-quaternary">{doneCount}/{subtasks.length}</span>}
          <button onClick={() => setAdding(true)} className="text-[12px] text-accent cursor-pointer hover:text-accent-hover transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>+ Add</button>
        </div>
      </div>

      {subtasks.length > 0 && (
        <div className="h-1 bg-bg-tertiary rounded-full overflow-hidden mb-2">
          <div className={`h-full rounded-full ${doneCount === subtasks.length ? 'bg-green' : 'bg-accent'}`} style={{ width: `${(doneCount / subtasks.length) * 100}%` }} />
        </div>
      )}

      <div className="space-y-0.5">
        {subtasks.map(sub => (
          <div key={sub.id} className="flex items-center gap-2.5 py-1.5 px-2 rounded-md hover:bg-bg-secondary transition-colors group" style={{ transitionDuration: 'var(--duration-instant)' }}>
            <button onClick={() => update.mutate({ id: sub.id, data: { status: sub.status === 'done' ? TaskStatus.TODO : TaskStatus.DONE } })}
              className="w-[14px] h-[14px] rounded-full border-[1.5px] flex items-center justify-center shrink-0 cursor-pointer"
              style={{ borderColor: sub.status === 'done' ? '#30D158' : 'rgba(255,245,235,0.15)', backgroundColor: sub.status === 'done' ? '#30D158' : 'transparent' }}>
              {sub.status === 'done' && <svg width="7" height="5" viewBox="0 0 10 8" fill="none"><path d="M1 4L3.5 6.5L9 1" stroke="#14130E" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>}
            </button>
            <span className={`text-[14px] flex-1 ${sub.status === 'done' ? 'text-text-quaternary line-through' : 'text-text-secondary'}`}>{sub.title}</span>
          </div>
        ))}
      </div>

      {adding && (
        <div className="flex items-center gap-2 mt-1 px-2">
          <div className="w-[14px] h-[14px] rounded-full border-[1.5px] border-accent/30 shrink-0" />
          <input value={newTitle} onChange={e => setNewTitle(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') addSubtask(); if (e.key === 'Escape') { setAdding(false); setNewTitle(''); } }}
            autoFocus placeholder="Subtask title..." className="flex-1 text-[14px] bg-transparent text-text outline-none placeholder:text-text-quaternary" />
        </div>
      )}
    </div>
  );
}
