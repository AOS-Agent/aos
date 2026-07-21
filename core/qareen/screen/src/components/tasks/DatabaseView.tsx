/**
 * DatabaseView — Notion-style headless table.
 * No internal toolbar — parent provides filtering/search.
 * Column headers clickable for sort, right-click for context menu.
 * Row hover: checkbox hides, "OPEN" pill appears.
 * All cells directly editable (selects always rendered).
 */

import { useState, useMemo, useEffect, useRef } from 'react';
import {
  User, Calendar, AlignLeft, Hash, CircleDot, FolderOpen, Clock,
  Eye, EyeOff, ArrowUp, ArrowDown, EyeOff as HideIcon, MessageSquare,
} from 'lucide-react';
import { type Task } from '@/hooks/useWork';
import { useUpdateTask } from '@/hooks/useTasks';
import { Tag } from '@/components/primitives/Tag';
import { TaskStatus, TaskPriority } from '@/lib/types';
import { format, isPast, isToday, isTomorrow, differenceInDays } from 'date-fns';
import type { LucideIcon } from 'lucide-react';

const PRI: Record<number, string> = { 1: '#FF453A', 2: '#FFD60A', 3: '#6B6560', 4: '#0A84FF', 5: '#4A4540' };
const STAT_COLOR: Record<string, string> = { triage: '#BF5AF2', backlog: '#6B6560', todo: '#6B6560', active: '#0A84FF', waiting: '#FFD60A', in_review: '#BF5AF2', done: '#30D158', cancelled: '#4A4540' };
const STAT_LABEL: Record<string, string> = { triage: 'Triage', backlog: 'Backlog', todo: 'Todo', active: 'Active', waiting: 'Waiting', in_review: 'In Review', done: 'Done', cancelled: 'Cancelled' };

function formatDue(iso: string) {
  const d = new Date(iso);
  if (isToday(d)) return { text: 'Today', overdue: false };
  if (isTomorrow(d)) return { text: 'Tomorrow', overdue: false };
  if (isPast(d)) return { text: `${Math.abs(differenceInDays(d, new Date()))}d overdue`, overdue: true };
  const days = differenceInDays(d, new Date());
  return days <= 7 ? { text: `in ${days}d`, overdue: false } : { text: format(d, 'MMM d'), overdue: false };
}

interface ColDef { id: string; label: string; icon: LucideIcon; width: number; flex?: boolean; defaultOn?: boolean; }

const ALL_COLS: ColDef[] = [
  { id: 'title', label: 'Task', icon: AlignLeft, width: 0, flex: true, defaultOn: true },
  { id: 'status', label: 'Status', icon: CircleDot, width: 110, defaultOn: true },
  { id: 'priority', label: 'Priority', icon: Hash, width: 80, defaultOn: true },
  { id: 'project', label: 'Project', icon: FolderOpen, width: 100, defaultOn: true },
  { id: 'due', label: 'Due', icon: Calendar, width: 100, defaultOn: true },
  { id: 'assigned_to', label: 'Assignee', icon: User, width: 100, defaultOn: true },
  { id: 'tags', label: 'Tags', icon: Hash, width: 130, defaultOn: false },
  { id: 'activity', label: 'Activity', icon: MessageSquare, width: 70, defaultOn: true },
  { id: 'created', label: 'Created', icon: Clock, width: 75, defaultOn: false },
];

type SortKey = { field: string; dir: 'asc' | 'desc' };
const selectCls = "w-full h-full bg-transparent text-[11px] text-text-tertiary outline-none cursor-pointer appearance-none hover:text-text-secondary";

// ── Cell components ──────────────────────────────────────────────────────

function TitleCell({ task }: { task: Task }) {
  const upd = useUpdateTask();
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(task.title);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { setVal(task.title); }, [task.title]);
  useEffect(() => { if (editing) { ref.current?.focus(); ref.current?.select(); } }, [editing]);
  const save = () => { if (val.trim() && val !== task.title) upd.mutate({ id: task.id, data: { title: val.trim() } }); setEditing(false); };
  if (editing) return <input ref={ref} value={val} onChange={e => setVal(e.target.value)} onBlur={save}
    onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') { setVal(task.title); setEditing(false); } }}
    className="w-full bg-transparent text-[13px] text-text outline-none" />;
  const done = task.status === 'done';
  return <span onClick={() => setEditing(true)} className={`text-[13px] truncate cursor-text ${done ? 'text-text-quaternary line-through' : 'text-text-secondary hover:text-text'}`}>{task.title}</span>;
}

function StatusCell({ task }: { task: Task }) {
  const upd = useUpdateTask();
  return <div className="flex items-center gap-1.5 w-full">
    <div className="w-[6px] h-[6px] rounded-full shrink-0" style={{ backgroundColor: STAT_COLOR[task.status] }} />
    <select value={task.status} onChange={e => upd.mutate({ id: task.id, data: { status: e.target.value as TaskStatus } })} className={selectCls}>
      <option value="triage">Triage</option><option value="backlog">Backlog</option>
      <option value="todo">Todo</option><option value="active">Active</option><option value="waiting">Waiting</option>
      <option value="in_review">In Review</option>
      <option value="done">Done</option><option value="cancelled">Cancelled</option>
    </select>
  </div>;
}

function PriorityCell({ task }: { task: Task }) {
  const upd = useUpdateTask();
  return <div className="flex items-center gap-1.5 w-full">
    <div className="w-[5px] h-[5px] rounded-full shrink-0" style={{ backgroundColor: PRI[task.priority] }} />
    <select value={task.priority} onChange={e => upd.mutate({ id: task.id, data: { priority: Number(e.target.value) as TaskPriority } })} className={selectCls}>
      <option value={1}>P1</option><option value={2}>P2</option><option value={3}>P3</option><option value={4}>P4</option><option value={5}>P5</option>
    </select>
  </div>;
}

function ProjectCell({ task, projects }: { task: Task; projects: string[] }) {
  const upd = useUpdateTask();
  return <select value={task.project ?? ''} onChange={e => upd.mutate({ id: task.id, data: { project: e.target.value || undefined } as any })} className={selectCls}>
    <option value="">—</option>{projects.map(p => <option key={p} value={p}>{p}</option>)}
  </select>;
}

function DueCell({ task }: { task: Task }) {
  const upd = useUpdateTask();
  const [picking, setPicking] = useState(false);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { if (picking) ref.current?.showPicker?.(); }, [picking]);
  if (picking) return <input ref={ref} type="date" defaultValue={task.due?.split('T')[0] ?? ''} onBlur={() => setPicking(false)}
    onChange={e => { upd.mutate({ id: task.id, data: { due: e.target.value || undefined } as any }); setPicking(false); }}
    className="w-full bg-transparent text-[11px] text-text-secondary outline-none cursor-pointer" />;
  const due = task.due ? formatDue(task.due) : null;
  return <span onClick={() => setPicking(true)} className={`text-[11px] cursor-pointer ${due?.overdue ? 'text-red font-[510]' : 'text-text-quaternary hover:text-text-tertiary'}`}>{due?.text ?? '—'}</span>;
}

function AssigneeCell({ task }: { task: Task }) {
  const upd = useUpdateTask();
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(task.assigned_to ?? '');
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { setVal(task.assigned_to ?? ''); }, [task.assigned_to]);
  useEffect(() => { if (editing) ref.current?.focus(); }, [editing]);
  const save = () => { upd.mutate({ id: task.id, data: { assigned_to: val.trim() || undefined } as any }); setEditing(false); };
  if (editing) return <input ref={ref} value={val} onChange={e => setVal(e.target.value)} onBlur={save}
    onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') { setVal(task.assigned_to ?? ''); setEditing(false); } }}
    placeholder="Assign..." className="w-full bg-transparent text-[11px] text-text outline-none" />;
  return <span onClick={() => setEditing(true)} className="text-[11px] text-text-quaternary hover:text-text-tertiary cursor-text truncate flex items-center gap-1">
    {task.assigned_to ? <><User className="w-[10px] h-[10px] shrink-0" />{task.assigned_to}</> : '—'}
  </span>;
}

// ── Main ─────────────────────────────────────────────────────────────────

export function DatabaseView({ tasks, onSelect, selectedId, projects = [], groupBy = '', onGroupByChange, visibleCols }: {
  tasks: Task[]; onSelect: (t: Task) => void; selectedId?: string; projects?: string[];
  groupBy?: string; onGroupByChange?: (g: string) => void; visibleCols?: Set<string>;
}) {
  const upd = useUpdateTask();
  const [sort, setSort] = useState<SortKey>({ field: 'priority', dir: 'asc' });
  const [colMenu, setColMenu] = useState<{ colId: string; x: number; y: number } | null>(null);

  const effectiveCols = visibleCols ?? new Set(ALL_COLS.filter(c => c.defaultOn).map(c => c.id));
  const columns = ALL_COLS.filter(c => effectiveCols.has(c.id));

  const sorted = useMemo(() => {
    return [...tasks].sort((a, b) => {
      let cmp = 0;
      const f = sort.field;
      if (f === 'title') cmp = a.title.localeCompare(b.title);
      else if (f === 'priority') cmp = a.priority - b.priority;
      else if (f === 'status') cmp = (a.status ?? '').localeCompare(b.status ?? '');
      else if (f === 'project') cmp = (a.project ?? '').localeCompare(b.project ?? '');
      else if (f === 'due') cmp = (a.due ? new Date(a.due).getTime() : Infinity) - (b.due ? new Date(b.due).getTime() : Infinity);
      else if (f === 'assigned_to') cmp = (a.assigned_to ?? '').localeCompare(b.assigned_to ?? '');
      else if (f === 'created') cmp = new Date(a.created).getTime() - new Date(b.created).getTime();
      return sort.dir === 'asc' ? cmp : -cmp;
    });
  }, [tasks, sort]);

  const groups = useMemo(() => {
    if (!groupBy) return [{ label: '', tasks: sorted }];
    const map = new Map<string, Task[]>();
    for (const t of sorted) {
      const key = (groupBy === 'project' ? t.project : groupBy === 'status' ? t.status : groupBy === 'assigned_to' ? t.assigned_to : '') ?? '(none)';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(t);
    }
    return [...map.entries()].map(([label, tasks]) => ({ label, tasks }));
  }, [sorted, groupBy]);

  // Close column menu on outside click
  useEffect(() => {
    if (!colMenu) return;
    const handler = () => setColMenu(null);
    setTimeout(() => document.addEventListener('click', handler), 0);
    return () => document.removeEventListener('click', handler);
  }, [colMenu]);

  const renderCell = (task: Task, colId: string) => {
    switch (colId) {
      case 'title': return <TitleCell task={task} />;
      case 'status': return <StatusCell task={task} />;
      case 'priority': return <PriorityCell task={task} />;
      case 'project': return <ProjectCell task={task} projects={projects} />;
      case 'due': return <DueCell task={task} />;
      case 'assigned_to': return <AssigneeCell task={task} />;
      case 'tags': return task.tags?.length ? <div className="flex gap-1 overflow-hidden">{task.tags.slice(0, 2).map(t => <Tag key={t} label={t} color="gray" />)}</div> : <span className="text-[11px] text-text-quaternary opacity-30">—</span>;
      case 'activity': return task.activity_count ? (
        <span className="inline-flex items-center gap-1 text-[11px] text-text-quaternary font-mono" title={task.last_activity?.body}>
          <MessageSquare className="w-[11px] h-[11px]" />{task.activity_count}
        </span>
      ) : <span className="text-[11px] text-text-quaternary opacity-30">—</span>;
      case 'created': return <span className="text-[10px] text-text-quaternary font-mono">{new Date(task.created).toLocaleDateString('en', { month: 'short', day: 'numeric' })}</span>;
      default: return null;
    }
  };

  return (
    <div className="h-full flex flex-col">
      {/* Column headers — click to sort, right-click for menu */}
      <div className="flex items-center h-8 border-b border-border-secondary mx-1 shrink-0">
        <div className="w-10 shrink-0" />
        {columns.map(col => {
          const Icon = col.icon;
          const isActive = sort.field === col.id;
          return (
            <button key={col.id}
              onClick={() => setSort(prev => prev.field === col.id ? { field: col.id, dir: prev.dir === 'asc' ? 'desc' : 'asc' } : { field: col.id, dir: 'asc' })}
              onContextMenu={e => { e.preventDefault(); setColMenu({ colId: col.id, x: e.clientX, y: e.clientY }); }}
              className={`flex items-center gap-1.5 text-[10px] font-[590] uppercase tracking-[0.04em] cursor-pointer h-full px-2 transition-colors duration-75 ${isActive ? 'text-text-tertiary' : 'text-text-quaternary hover:text-text-tertiary'}`}
              style={col.flex ? { flex: 1, minWidth: 0 } : { width: col.width, flexShrink: 0 }}>
              <Icon className="w-3 h-3 opacity-40" />{col.label}
              {isActive && <span className="text-accent text-[8px]">{sort.dir === 'asc' ? '▲' : '▼'}</span>}
            </button>
          );
        })}
      </div>

      {/* Column header context menu */}
      {colMenu && (
        <div className="fixed bg-bg-panel border border-border-secondary rounded-lg shadow-[0_4px_20px_rgba(0,0,0,0.4)] z-[100] py-1 w-[180px]"
          style={{ left: colMenu.x, top: colMenu.y }} onClick={e => e.stopPropagation()}>
          <p className="text-[10px] font-[590] text-text-quaternary uppercase tracking-wider px-3 py-1">
            {ALL_COLS.find(c => c.id === colMenu.colId)?.label}
          </p>
          <div className="border-t border-border my-1" />
          <button onClick={() => { setSort({ field: colMenu.colId, dir: 'asc' }); setColMenu(null); }}
            className="flex items-center gap-2 w-full px-3 py-1.5 text-[11px] text-text-tertiary hover:bg-bg-secondary cursor-pointer">
            <ArrowUp className="w-3 h-3" />Sort ascending
          </button>
          <button onClick={() => { setSort({ field: colMenu.colId, dir: 'desc' }); setColMenu(null); }}
            className="flex items-center gap-2 w-full px-3 py-1.5 text-[11px] text-text-tertiary hover:bg-bg-secondary cursor-pointer">
            <ArrowDown className="w-3 h-3" />Sort descending
          </button>
          <div className="border-t border-border my-1" />
          <button onClick={() => { onGroupByChange?.(colMenu.colId === groupBy ? '' : colMenu.colId); setColMenu(null); }}
            className="flex items-center gap-2 w-full px-3 py-1.5 text-[11px] text-text-tertiary hover:bg-bg-secondary cursor-pointer">
            {groupBy === colMenu.colId ? 'Ungroup' : 'Group by this'}
          </button>
          {/* Hide column — managed by parent Settings popover */}
        </div>
      )}

      {/* Rows */}
      <div className="flex-1 overflow-y-auto">
        {groups.map((group, gi) => (
          <div key={gi}>
            {group.label && (
              <div className="flex items-center gap-2 h-8 px-3 bg-bg-secondary/50 border-b border-border sticky top-0 z-10">
                {groupBy === 'status' && <div className="w-2 h-2 rounded-full" style={{ backgroundColor: STAT_COLOR[group.label] }} />}
                <span className="text-[11px] font-[510] text-text-tertiary">
                  {groupBy === 'status' ? (STAT_LABEL[group.label] ?? group.label) : group.label}
                </span>
                <span className="text-[10px] font-mono text-text-quaternary">{group.tasks.length}</span>
              </div>
            )}
            {group.tasks.map(task => {
              const done = task.status === 'done';
              return (
                <div key={task.id}
                  className={`flex items-center h-10 border-b border-border/50 mx-1 rounded-md group transition-colors duration-75 ${
                    selectedId === task.id ? 'bg-bg-tertiary' : 'hover:bg-bg-secondary/50'
                  }`}>
                  {/* Left zone: checkbox normally, OPEN pill on hover */}
                  <div className="w-10 flex items-center justify-center shrink-0 relative">
                    <button onClick={() => upd.mutate({ id: task.id, data: { status: done ? TaskStatus.TODO : TaskStatus.DONE } })}
                      className="w-4 h-4 rounded-full border-[1.5px] flex items-center justify-center cursor-pointer group-hover:invisible"
                      style={{ borderColor: done ? '#30D158' : 'rgba(255,245,235,0.15)', backgroundColor: done ? '#30D158' : 'transparent' }}>
                      {done && <svg width="8" height="6" viewBox="0 0 10 8" fill="none"><path d="M1 4L3.5 6.5L9 1" stroke="#14130E" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>}
                    </button>
                    <button onClick={() => onSelect(task)}
                      className="absolute inset-0 flex items-center justify-center invisible group-hover:visible cursor-pointer" title="Open">
                      <span className="text-[8px] font-[590] text-accent bg-accent/10 px-1.5 py-0.5 rounded-full uppercase tracking-wider">Open</span>
                    </button>
                  </div>
                  {/* Cells */}
                  {columns.map(col => (
                    <div key={col.id} className="px-2 h-full flex items-center overflow-hidden"
                      style={col.flex ? { flex: 1, minWidth: 0 } : { width: col.width, flexShrink: 0 }}>
                      {renderCell(task, col.id)}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
