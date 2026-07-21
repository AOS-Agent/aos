/**
 * Today — the Work landing.
 *
 * Organized by AREA (each Goal is an area: AOS, Deen Over Dunya, …). Every area
 * carries a color identity that propagates to its project cards and task rows, so
 * affiliation is always visible. Areas collapse so you can focus one at a time.
 *
 *   Area (Goal) → Projects → Tasks
 */

import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useWork, useWorkCounts, type Task, type Project } from '@/hooks/useWork';
import { useUpdateTask } from '@/hooks/useTasks';
import { useTaskOverlay } from '@/components/tasks/TaskOverlayContext';
import { TaskStatus } from '@/lib/types';
import { areaTone, taskGoalId, type AreaTone } from '@/lib/areaStyle';
import { format, isPast, isToday, isTomorrow, differenceInDays } from 'date-fns';

interface Goal {
  id: string;
  title: string;
  description?: string;
  status?: string;
  key_results?: Array<{ title: string; current: number; target: number }>;
}

function formatDue(iso: string) {
  const d = new Date(iso);
  if (isToday(d)) return { text: 'Today', overdue: false };
  if (isTomorrow(d)) return { text: 'Tomorrow', overdue: false };
  if (isPast(d)) return { text: `${Math.abs(differenceInDays(d, new Date()))}d overdue`, overdue: true };
  const days = differenceInDays(d, new Date());
  return days <= 7 ? { text: `in ${days}d`, overdue: false } : { text: format(d, 'MMM d'), overdue: false };
}

function TaskRow({ task, tone, showProject, onToggle }: { task: Task; tone: AreaTone; showProject?: boolean; onToggle: () => void }) {
  const { openTask } = useTaskOverlay();
  const done = task.status === 'done';
  const due = task.due ? formatDue(task.due) : null;
  return (
    <div
      onClick={() => openTask(task.id)}
      className="flex items-center gap-3 h-10 px-2 rounded-lg cursor-pointer hover:bg-bg-secondary transition-colors duration-75 group"
    >
      <button
        onClick={e => { e.stopPropagation(); onToggle(); }}
        aria-label={done ? 'Mark not done' : 'Mark done'}
        className="w-[16px] h-[16px] rounded-full border-[1.5px] flex items-center justify-center shrink-0 cursor-pointer"
        style={{ borderColor: done ? '#30D158' : 'rgba(255,245,235,0.15)', backgroundColor: done ? '#30D158' : 'transparent' }}
      >
        {done && <svg width="8" height="6" viewBox="0 0 10 8" fill="none"><path d="M1 4L3.5 6.5L9 1" stroke="#14130E" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>}
      </button>
      {/* area dot — color = which area this task belongs to */}
      <span className={`w-[6px] h-[6px] rounded-full shrink-0 ${tone.dot}`} />
      <span className={`flex-1 min-w-0 text-[15px] truncate ${done ? 'text-text-quaternary line-through' : 'text-text-secondary'}`}>{task.title}</span>
      <div className="flex items-center gap-2 text-[12px] text-text-quaternary opacity-0 group-hover:opacity-100 transition-opacity duration-75">
        {showProject && task.project && <span>{task.project}</span>}
        {due && <span className={due.overdue ? 'text-red' : ''}>{due.text}</span>}
      </div>
    </div>
  );
}

export default function TodayPage({ onProjectClick }: { onProjectClick?: (projectId: string) => void }) {
  const { data, isLoading } = useWork();
  const { counts } = useWorkCounts();
  const update = useUpdateTask();
  const now = new Date();

  const tasks = (data?.tasks ?? []) as Task[];
  const projects = (data?.projects ?? []) as Project[];
  const goals = (data?.goals ?? []) as Goal[];
  const inboxCount = (data?.inbox ?? []).length;

  const notDone = (t: Task) => t.status !== 'done' && t.status !== 'cancelled';
  const overdue = tasks.filter(t => t.due && isPast(new Date(t.due)) && !isToday(new Date(t.due)) && notDone(t));
  const activeTasks = tasks.filter(t => t.status === 'active');
  // Header chips use the authoritative whole-table counts (not the bounded
  // task list) so "N active / N todo" is always true.
  const activeCount = counts.active;
  const todoCount = counts.todo;
  const doneToday = tasks.filter(t => t.status === 'done' && t.completed && isToday(new Date(t.completed)));

  const toggleDone = (t: Task, to: TaskStatus) => update.mutate({ id: t.id, data: { status: to } });

  // ── Build areas from goals ──
  const areas = goals
    .map((goal, i) => {
      const tone = areaTone(goal.id, i);
      const areaProjects = projects.filter(p => p.goal === goal.id);
      const total = areaProjects.reduce((s, p) => s + (p.task_count ?? 0), 0);
      const done = areaProjects.reduce((s, p) => s + (p.done_count ?? 0), 0);
      const pct = total > 0 ? Math.round((done / total) * 100) : 0;
      const activeInArea = activeTasks.filter(t => taskGoalId(t, projects) === goal.id);
      return { goal, tone, areaProjects, total, done, pct, activeInArea };
    })
    .filter(a => a.areaProjects.length > 0 || a.activeInArea.length > 0)
    .sort((a, b) => b.activeInArea.length - a.activeInArea.length || b.total - a.total);

  const toneFor = (t: Task) => {
    const gid = taskGoalId(t, projects);
    const idx = goals.findIndex(g => g.id === gid);
    return areaTone(gid, idx < 0 ? 0 : idx);
  };

  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggle = (id: string) => setCollapsed(prev => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });
  const [showDone, setShowDone] = useState(false);

  if (isLoading) return <div className="flex items-center justify-center h-full"><p className="text-text-quaternary">Loading…</p></div>;

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[880px] mx-auto px-6 py-8">
        {/* Header */}
        <h2 className="text-[26px] font-[600] text-text mb-0.5">Today</h2>
        <p className="text-[15px] text-text-tertiary mb-6">{format(now, 'EEEE, MMMM d')}</p>

        {/* Summary stats */}
        <div className="flex items-center gap-6 mb-8 text-[14px]">
          {overdue.length > 0 && <div className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-red" /><span className="text-red font-[510]">{overdue.length} overdue</span></div>}
          <div className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-blue" /><span className="text-text-tertiary">{activeCount} active</span></div>
          <div className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-text-quaternary" /><span className="text-text-tertiary">{todoCount} todo</span></div>
          {inboxCount > 0 && <div className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-purple" /><span className="text-text-tertiary">{inboxCount} to triage</span></div>}
          {doneToday.length > 0 && <div className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-green" /><span className="text-green">{doneToday.length} done today</span></div>}
        </div>

        {/* ── Overdue (cross-area alert) ── */}
        {overdue.length > 0 && (
          <div className="mb-8">
            <div className="flex items-center gap-2 px-1 mb-1.5">
              <h3 className="text-[12px] font-[590] uppercase tracking-[0.06em] text-red">Overdue</h3>
              <span className="text-[12px] font-mono text-text-quaternary">{overdue.length}</span>
            </div>
            {overdue.map(t => <TaskRow key={t.id} task={t} tone={toneFor(t)} showProject onToggle={() => toggleDone(t, TaskStatus.DONE)} />)}
          </div>
        )}

        {/* ── Areas ── */}
        {areas.map(area => {
          const isCollapsed = collapsed.has(area.goal.id);
          return (
            <div key={area.goal.id} className="mb-7">
              {/* Area header */}
              <button onClick={() => toggle(area.goal.id)} className="w-full flex items-center gap-2.5 mb-3 cursor-pointer">
                {isCollapsed ? <ChevronRight className="w-3.5 h-3.5 text-text-quaternary shrink-0" /> : <ChevronDown className="w-3.5 h-3.5 text-text-quaternary shrink-0" />}
                <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${area.tone.dot}`} />
                <span className="text-[17px] font-[600] text-text truncate">{area.goal.title}</span>
                <span className="flex-1" />
                <span className="text-[12px] font-mono text-text-quaternary shrink-0">{area.total} task{area.total === 1 ? '' : 's'}</span>
                <span className={`text-[13px] font-mono font-[510] shrink-0 ${area.tone.text}`}>{area.pct}%</span>
              </button>

              {/* Collapsible body */}
              <div className="grid transition-[grid-template-rows] duration-200 ease-out" style={{ gridTemplateRows: isCollapsed ? '0fr' : '1fr' }}>
                <div className="overflow-hidden">
                  <div className="pl-6">
                    {/* Project cards */}
                    {area.areaProjects.length > 0 && (
                      <div className="flex flex-wrap gap-2 mb-3">
                        {area.areaProjects.map(proj => {
                          const total = proj.task_count ?? 0;
                          const done = proj.done_count ?? 0;
                          const pct = total > 0 ? Math.round((done / total) * 100) : 0;
                          return (
                            <button
                              key={proj.id}
                              onClick={() => onProjectClick?.(proj.id)}
                              className="px-3 py-2 rounded-lg bg-bg-secondary border border-border min-w-[150px] text-left hover:bg-bg-tertiary transition-colors duration-75 cursor-pointer"
                            >
                              <div className="flex items-center gap-2 mb-1.5">
                                <span className={`w-[5px] h-[5px] rounded-full shrink-0 ${area.tone.dot}`} />
                                <span className="text-[14px] font-[510] text-text-secondary truncate flex-1">{proj.title}</span>
                                <span className="text-[11px] font-mono text-text-quaternary shrink-0">{done}/{total}</span>
                              </div>
                              <div className="h-1 bg-bg-tertiary rounded-full overflow-hidden">
                                <div className={`h-full rounded-full ${area.tone.dot}`} style={{ width: `${pct}%` }} />
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    )}

                    {/* Active tasks in this area */}
                    {area.activeInArea.length > 0 ? (
                      <div className="mb-1">
                        {area.activeInArea.map(t => <TaskRow key={t.id} task={t} tone={area.tone} onToggle={() => toggleDone(t, TaskStatus.DONE)} />)}
                      </div>
                    ) : (
                      <p className="text-[13px] text-text-quaternary px-2 py-1.5">Nothing active here right now.</p>
                    )}
                  </div>
                </div>
              </div>
            </div>
          );
        })}

        {/* ── Completed today ── */}
        {doneToday.length > 0 && (
          <div className="mt-6 pt-4 border-t border-border">
            <button onClick={() => setShowDone(!showDone)} className="flex items-center gap-2 px-2 cursor-pointer">
              {showDone ? <ChevronDown className="w-3 h-3 text-text-quaternary" /> : <ChevronRight className="w-3 h-3 text-text-quaternary" />}
              <span className="text-[12px] font-[590] uppercase tracking-[0.06em] text-green">Completed today</span>
              <span className="text-[12px] font-mono text-text-quaternary">{doneToday.length}</span>
            </button>
            {showDone && doneToday.map(t => <TaskRow key={t.id} task={t} tone={toneFor(t)} onToggle={() => toggleDone(t, TaskStatus.TODO)} />)}
          </div>
        )}

        {/* Empty state */}
        {areas.length === 0 && overdue.length === 0 && (
          <div className="py-16 text-center">
            <p className="text-[17px] text-text-quaternary opacity-50">Nothing on the plate.</p>
            <p className="text-[13px] text-text-quaternary opacity-30 mt-1">Areas, projects, and tasks will appear here.</p>
          </div>
        )}
      </div>
    </div>
  );
}
