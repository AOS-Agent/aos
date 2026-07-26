/**
 * ProjectDetail — the compiled project brief, live.
 *
 * The operator's ask: "I want to be able to have that project in front of me so
 * that I can see as things happen." So this page leads with prose, not a
 * progress bar, and refreshes itself when the compiler recompiles the brief.
 *
 * Reading order (BRIEF-CONTRACT.md):
 *   header → where this stands → conflicts → next up / blockers → phases →
 *   artifacts → recent activity
 *
 * Live: `useSSE` listens for `project.brief.updated` and invalidates
 * `['project-brief', id]`. Nothing here polls; nothing here opens a second
 * stream.
 *
 * Degradation: the brief endpoint may not be deployed on a given machine. When
 * it can't answer, `unavailable` is true and the page falls back to the plain
 * status-grouped task list it rendered before — quietly, with no error chrome
 * for a surface the operator didn't come for.
 */

import { useState, useEffect, useCallback } from 'react';
import { ArrowLeft } from 'lucide-react';
import { useWork, type Task, type Goal } from '@/hooks/useWork';
import { useUpdateTask, useProjectTasks } from '@/hooks/useTasks';
import { useProjectBrief } from '@/hooks/useProjectBrief';
import { useTaskOverlay } from '@/components/tasks/TaskOverlayContext';
import { TaskStatus } from '@/lib/types';
import { areaTone } from '@/lib/areaStyle';
import GitView from '@/pages/project/GitView';
import { BriefHeader } from '@/components/project/brief/BriefHeader';
import { Narrative } from '@/components/project/brief/Narrative';
import { Conflicts } from '@/components/project/brief/Conflicts';
import { NextUp, Blockers } from '@/components/project/brief/NextUp';
import { Phases, StatusGroups } from '@/components/project/brief/Phases';
import { Artifacts } from '@/components/project/brief/Artifacts';
import { Timeline } from '@/components/project/brief/Timeline';

type BackLabel = 'today' | 'tasks' | 'projects' | 'goals';

const BACK_LABELS: Record<BackLabel, string> = {
  today: 'Today',
  tasks: 'Tasks',
  projects: 'Projects',
  goals: 'Goals',
};

/**
 * True for ~2.5s after the brief's `compiled_at` changes. Watching the compiled
 * timestamp means the pulse fires on a real recompile — not merely on a
 * refetch that returned identical bytes.
 */
function useRecompilePulse(compiledAt: string | undefined): boolean {
  const [seen, setSeen] = useState<string | undefined>(undefined);
  const [pulsing, setPulsing] = useState(false);

  // Adjust state during render rather than in an effect — this is a value
  // derived from a changing prop, not a subscription to an external system.
  if (compiledAt && seen !== compiledAt) {
    setSeen(compiledAt);
    if (seen !== undefined) setPulsing(true);   // the first load isn't news
  }

  useEffect(() => {
    if (!pulsing) return;
    const t = setTimeout(() => setPulsing(false), 2500);
    return () => clearTimeout(t);
  }, [pulsing]);

  return pulsing;
}

export default function ProjectDetail({ projectId, backLabel, onBack }: { projectId: string; backLabel: BackLabel; onBack: () => void }) {
  const { data, isLoading } = useWork();
  const update = useUpdateTask();
  const { openTask } = useTaskOverlay();

  const projects = data?.projects ?? [];
  const goals = (data?.goals ?? []) as Goal[];

  // Full task list for THIS project (uncapped) — the global /api/work caps at 200.
  const { data: projectTasks = [], isLoading: tasksLoading } = useProjectTasks(projectId);
  const { brief, unavailable, isLoading: briefLoading } = useProjectBrief(projectId);

  const proj = projects.find(p => p.id === projectId);
  const goalIdx = goals.findIndex(g => g.id === proj?.goal);
  const goal = goalIdx >= 0 ? goals[goalIdx] : undefined;          // resolve id -> goal.title
  const tone = areaTone(proj?.goal, goalIdx < 0 ? 0 : goalIdx);

  const justUpdated = useRecompilePulse(brief?.compiled_at);

  // AUTHORITATIVE counts — prefer the brief, fall back to the project record.
  // NEVER derive from rows (they are paged).
  const total = brief?.task_count ?? proj?.task_count ?? 0;
  const done = brief?.done_count ?? proj?.done_count ?? 0;
  const active = brief?.active_count ?? proj?.active_count ?? 0;

  const toggle = useCallback(
    (t: Task) => update.mutate({ id: t.id, data: { status: t.status === 'done' ? TaskStatus.TODO : TaskStatus.DONE } }),
    [update],
  );

  // Sub-view toggle — Brief (default) vs the Git/Ship cockpit. LOCAL state only:
  // we never touch Work.tsx's ?tab/?project URL contract.
  const [view, setView] = useState<'brief' | 'git'>('brief');
  const repoLinked = !!proj?.path;

  // Close animation (DESIGN rule 7) — owned internally so Work's onBack stays a one-liner.
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
        <div className="max-w-[1320px] mx-auto px-6 py-8 space-y-4">
          <div className="h-8 w-[280px] rounded bg-bg-secondary animate-pulse" />
          <div className="h-4 w-[420px] rounded bg-bg-secondary animate-pulse" />
          <div className="h-24 w-full max-w-[70ch] rounded-[7px] bg-bg-secondary animate-pulse" />
        </div>
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
  const hasBrief = !!brief && !unavailable;
  // The brief owns phase grouping. Without phases (or without a brief at all)
  // the task list falls back to status groups — the pre-brief rendering.
  const usePhases = hasBrief && brief!.phases.length > 0;

  return (
    <div className={`h-full overflow-y-auto ${animClass}`} onAnimationEnd={() => { if (closing) onBack(); }}>
      <div className={`${gitActive ? 'max-w-[1200px]' : 'max-w-[1320px]'} mx-auto px-6 py-8 transition-[max-width] duration-300`}>
        {backLink}

        <div className="mt-6">
          <BriefHeader
            title={proj.title}
            goalTitle={brief?.goal_title ?? goal?.title ?? null}
            tone={tone}
            brief={brief}
            total={total}
            done={done}
            active={active}
            justUpdated={justUpdated}
          />
        </div>

        {/* Sub-view switcher — glass pill, mounts only for repo-linked projects. */}
        {repoLinked && (
          <div className="mb-8 flex">
            <div
              className="flex items-center gap-1 h-9 px-1 rounded-full border"
              style={{ background: 'var(--glass-bg)', backdropFilter: 'blur(12px)', borderColor: 'var(--glass-border)' }}
            >
              {(['brief', 'git'] as const).map(v => (
                <button
                  key={v}
                  onClick={() => setView(v)}
                  className={`px-3.5 h-7 rounded-full text-[14px] font-[510] cursor-pointer transition-all duration-150 ${
                    view === v ? 'bg-[rgba(255,245,235,0.10)] text-text' : 'text-text-tertiary hover:text-text-secondary'
                  }`}
                >
                  {v === 'brief' ? 'Brief' : 'Git'}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* ====================== GIT COCKPIT ====================== */}
        {gitActive && <GitView projectId={projectId} path={proj.path!} tone={tone} />}

        {/* ====================== BRIEF (default) ====================== */}
        {!gitActive && (
          <>
            {/* The narrative is the page's centre of gravity. While it compiles,
                hold its shape rather than collapsing the layout. */}
            {briefLoading && !brief && (
              <div className="mb-10 space-y-2.5 max-w-[70ch]">
                <div className="h-5 w-full rounded bg-bg-secondary animate-pulse" />
                <div className="h-5 w-[88%] rounded bg-bg-secondary animate-pulse" />
                <div className="h-5 w-[64%] rounded bg-bg-secondary animate-pulse" />
              </div>
            )}

            {hasBrief && (
              <>
                <Narrative brief={brief!} />
                <Conflicts conflicts={brief!.conflicts} onOpenTask={openTask} />

                {(brief!.next_up.length > 0 || brief!.blockers.length > 0) && (
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-6 gap-y-8 mb-10 items-start">
                    <NextUp items={brief!.next_up} onOpenTask={openTask} />
                    <Blockers blockers={brief!.blockers} onOpenTask={openTask} />
                  </div>
                )}
              </>
            )}

            {/* Tasks — by phase when the brief supplies them, by status otherwise. */}
            {tasksLoading && (
              <div className="space-y-1.5 mb-10">
                {[0, 1, 2, 3, 4, 5].map(i => (
                  <div key={i} className="h-10 rounded-lg bg-bg-secondary animate-pulse" style={{ opacity: 0.6 }} />
                ))}
              </div>
            )}

            {!tasksLoading && usePhases && (
              <Phases phases={brief!.phases} tasks={projectTasks} tone={tone} onToggleTask={toggle} />
            )}

            {!tasksLoading && !usePhases && projectTasks.length > 0 && (
              <div className="mb-10">
                <StatusGroups tasks={projectTasks} tone={tone} onToggleTask={toggle} />
              </div>
            )}

            {!tasksLoading && projectTasks.length === 0 && (
              <p className="text-[15px] text-text-quaternary py-8">No tasks in this project yet.</p>
            )}

            {hasBrief && (
              <>
                <Artifacts artifacts={brief!.artifacts} />
                <Timeline events={brief!.recent_activity} />
              </>
            )}
          </>
        )}
      </div>
      {keyframes}
    </div>
  );
}
