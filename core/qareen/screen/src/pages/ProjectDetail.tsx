/**
 * ProjectDetail — full-canvas project view.
 *
 * Opened from any list surface (Today, Projects, Goals) by drilling into a
 * project card. Renders as a real page that replaces the list while the active
 * tab stays mounted behind it — Back returns to exactly where you came from.
 *
 * Shows the project title, its GOAL by title (with area color), AUTHORITATIVE
 * progress (proj.task_count / done_count / active_count — never the capped
 * tasks[] array), and its tasks grouped by status, each toggleable.
 */

import { useState, useEffect, useCallback } from 'react';
import { ArrowLeft, ChevronDown, ChevronRight } from 'lucide-react';
import { useWork, type Task, type Goal } from '@/hooks/useWork';
import { useUpdateTask, useProjectTasks } from '@/hooks/useTasks';
import { useTaskOverlay } from '@/components/tasks/TaskOverlayContext';
import { TaskStatus } from '@/lib/types';
import { areaTone } from '@/lib/areaStyle';
import GitView from '@/pages/project/GitView';

type BackLabel = 'today' | 'tasks' | 'projects' | 'goals';

const BACK_LABELS: Record<BackLabel, string> = {
  today: 'Today',
  tasks: 'Tasks',
  projects: 'Projects',
  goals: 'Goals',
};

const STATUS_GROUPS: { status: Task['status']; label: string }[] = [
  { status: 'active', label: 'Active' },
  { status: 'todo', label: 'Todo' },
  { status: 'waiting', label: 'Waiting' },
  { status: 'done', label: 'Done' },
];

function TaskRow({ task, dot, onToggle }: { task: Task; dot: string; onToggle: () => void }) {
  const { openTask } = useTaskOverlay();
  const done = task.status === 'done';
  return (
    <div
      onClick={() => openTask(task.id)}
      className="flex items-center gap-3 h-10 px-2 rounded-lg cursor-pointer hover:bg-bg-secondary transition-colors duration-75"
    >
      <button
        onClick={e => { e.stopPropagation(); onToggle(); }}
        aria-label={done ? 'Mark not done' : 'Mark done'}
        className="w-[16px] h-[16px] rounded-full border-[1.5px] flex items-center justify-center shrink-0 cursor-pointer"
        style={{ borderColor: done ? '#30D158' : 'rgba(255,245,235,0.15)', backgroundColor: done ? '#30D158' : 'transparent' }}
      >
        {done && <svg width="8" height="6" viewBox="0 0 10 8" fill="none"><path d="M1 4L3.5 6.5L9 1" stroke="#14130E" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>}
      </button>
      <span className={`w-[6px] h-[6px] rounded-full shrink-0 ${dot}`} />
      <span className={`flex-1 min-w-0 text-[15px] truncate ${done ? 'text-text-quaternary line-through' : 'text-text-secondary'}`}>{task.title}</span>
    </div>
  );
}

export default function ProjectDetail({ projectId, backLabel, onBack }: { projectId: string; backLabel: BackLabel; onBack: () => void }) {
  const { data, isLoading } = useWork();
  const update = useUpdateTask();

  const projects = data?.projects ?? [];
  const goals = (data?.goals ?? []) as Goal[];
  // Full task list for THIS project (uncapped) — fixes the "481 count but 1 task shown" bug.
  const { data: projectTasks = [], isLoading: tasksLoading } = useProjectTasks(projectId);

  const proj = projects.find(p => p.id === projectId);
  const goalIdx = goals.findIndex(g => g.id === proj?.goal);
  const goal = goalIdx >= 0 ? goals[goalIdx] : undefined;          // fix#3: id -> goal.title
  const tone = areaTone(proj?.goal, goalIdx < 0 ? 0 : goalIdx);    // area color via project's goal

  // AUTHORITATIVE counts — fix#2, NEVER derive from rows (capped at 200):
  const total = proj?.task_count ?? 0;
  const done = proj?.done_count ?? 0;
  const active = proj?.active_count ?? 0;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const todoLeft = Math.max(0, total - done - active);

  // rows = the project's FULL task list (uncapped fetch); totals still come from proj.*_count
  const rows = projectTasks;
  const group = (s: Task['status']) => rows.filter(t => t.status === s);
  const toggle = (t: Task) => update.mutate({ id: t.id, data: { status: t.status === 'done' ? TaskStatus.TODO : TaskStatus.DONE } });

  const [showDone, setShowDone] = useState(false);

  // Sub-view toggle — Tasks (default) vs the Git/Ship cockpit. LOCAL state only:
  // we never touch Work.tsx's ?tab/?project URL contract (URL persistence of the
  // sub-view is deferred). The switcher mounts only when the project has a repo.
  const [view, setView] = useState<'tasks' | 'git'>('tasks');
  const repoLinked = !!proj?.path;

  // Close animation (rule 7) — owned internally so Work's onBack stays a one-liner.
  const [closing, setClosing] = useState(false);
  const handleClose = useCallback(() => setClosing(true), []);
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') handleClose(); };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [handleClose]);

  const backLink = (
    <button
      onClick={handleClose}
      className="flex items-center gap-1.5 text-[13px] text-text-quaternary hover:text-text-tertiary cursor-pointer"
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      <ArrowLeft className="w-3 h-3" />
      {BACK_LABELS[backLabel]}
    </button>
  );

  const keyframes = <style>{`@keyframes projIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}@keyframes projOut{from{opacity:1;transform:none}to{opacity:0;transform:translateY(6px)}}`}</style>;
  const animClass = closing ? 'animate-[projOut_160ms_ease-in]' : 'animate-[projIn_180ms_ease-out]';

  if (isLoading) {
    return (
      <div className={`h-full overflow-y-auto ${animClass}`} onAnimationEnd={() => { if (closing) onBack(); }}>
        <div className="flex items-center justify-center h-full"><p className="text-text-quaternary">Loading…</p></div>
        {keyframes}
      </div>
    );
  }

  if (!proj) {
    return (
      <div className={`h-full overflow-y-auto ${animClass}`} onAnimationEnd={() => { if (closing) onBack(); }}>
        <div className="max-w-[880px] mx-auto px-6 py-8">
          {backLink}
          <p className="text-[15px] text-text-quaternary mt-6">Project not found.</p>
        </div>
        {keyframes}
      </div>
    );
  }

  const gitActive = repoLinked && view === 'git';

  return (
    <div className={`h-full overflow-y-auto ${animClass}`} onAnimationEnd={() => { if (closing) onBack(); }}>
      <div className={`${gitActive ? 'max-w-[1200px]' : 'max-w-[880px]'} mx-auto px-6 py-8 transition-[max-width] duration-300`}>
        {/* Back */}
        {backLink}

        {/* Header — goal chip + project title */}
        <div className="mt-6 mb-6">
          <div className="flex items-center gap-2 mb-2">
            <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${tone.dot}`} />
            <span className={`text-[14px] ${tone.text} truncate`}>{goal?.title ?? 'No area'}</span>
          </div>
          <h1 className="text-[22px] font-[600] text-text">{proj.title}</h1>
        </div>

        {/* Progress */}
        <div className="mb-1.5 flex items-center gap-2.5">
          <div className="flex-1 h-1.5 bg-bg-tertiary rounded-full overflow-hidden">
            <div className={`h-full rounded-full ${tone.dot}`} style={{ width: `${pct}%` }} />
          </div>
          <span className="text-[12px] font-mono text-text-quaternary tabular-nums shrink-0">{done}/{total}</span>
          <span className={`text-[13px] font-mono font-[510] shrink-0 ${tone.text}`}>{pct}%</span>
        </div>
        <p className="text-[13px] text-text-quaternary mb-6">{active} active · {todoLeft} todo · {done} done</p>

        {/* Sub-view switcher — glass pill, left-aligned, mounts only for repo-linked
            projects. Reuses Work.tsx's tab-pill look inline (subtle, non-permanent). */}
        {repoLinked && (
          <div className="mb-8 flex">
            <div
              className="flex items-center gap-1 h-9 px-1 rounded-full border"
              style={{ background: 'var(--glass-bg)', backdropFilter: 'blur(12px)', borderColor: 'var(--glass-border)' }}
            >
              {(['tasks', 'git'] as const).map(v => (
                <button
                  key={v}
                  onClick={() => setView(v)}
                  className={`px-3.5 h-7 rounded-full text-[14px] font-[510] cursor-pointer transition-all duration-150 ${
                    view === v ? 'bg-[rgba(255,245,235,0.10)] text-text' : 'text-text-tertiary hover:text-text-secondary'
                  }`}
                >
                  {v === 'tasks' ? 'Tasks' : 'Git'}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* ====================== GIT COCKPIT ====================== */}
        {gitActive && <GitView projectId={projectId} path={proj.path!} tone={tone} />}

        {/* ====================== TASKS (default) ====================== */}
        {/* Loading skeleton — the per-project task fetch can take ~2s on big projects,
            so show placeholder rows instead of an empty page that reads as "nothing here". */}
        {(!repoLinked || view === 'tasks') && tasksLoading && (
          <div className="space-y-1.5">
            {[0, 1, 2, 3, 4, 5].map(i => <div key={i} className="h-10 rounded-lg bg-bg-secondary animate-pulse" style={{ opacity: 0.6 }} />)}
          </div>
        )}

        {/* Grouped tasks */}
        {(!repoLinked || view === 'tasks') && !tasksLoading && STATUS_GROUPS.map(({ status, label }) => {
          const items = group(status);
          if (items.length === 0) return null;
          const isDoneGroup = status === 'done';
          const open = !isDoneGroup || showDone;
          return (
            <div key={status} className="mb-6">
              {isDoneGroup ? (
                <button onClick={() => setShowDone(!showDone)} className="flex items-center gap-2 px-1 mb-1.5 cursor-pointer">
                  {showDone ? <ChevronDown className="w-3 h-3 text-text-quaternary" /> : <ChevronRight className="w-3 h-3 text-text-quaternary" />}
                  <span className="text-[12px] font-[590] uppercase tracking-[0.06em] text-text-tertiary">{label}</span>
                  <span className="text-[12px] font-mono text-text-quaternary">{items.length}</span>
                </button>
              ) : (
                <div className="flex items-center gap-2 px-1 mb-1.5">
                  <span className="text-[12px] font-[590] uppercase tracking-[0.06em] text-text-tertiary">{label}</span>
                  <span className="text-[12px] font-mono text-text-quaternary">{items.length}</span>
                </div>
              )}
              {open && items.map(t => <TaskRow key={t.id} task={t} dot={tone.dot} onToggle={() => toggle(t)} />)}
            </div>
          );
        })}

        {(!repoLinked || view === 'tasks') && !tasksLoading && rows.length === 0 && (
          <p className="text-[15px] text-text-quaternary py-8 text-center">No tasks in this project yet.</p>
        )}
      </div>
      {keyframes}
    </div>
  );
}
