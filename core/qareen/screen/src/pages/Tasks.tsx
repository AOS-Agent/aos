import { useState, useMemo } from 'react';
import { Plus, CheckSquare, X } from 'lucide-react';
import { useWork, type Task } from '@/hooks/useWork';
import { useCreateTask, useUpdateTask } from '@/hooks/useTasks';
import { ErrorBanner } from '@/components/primitives/ErrorBanner';
import { EmptyState } from '@/components/primitives/EmptyState';
import { Tag } from '@/components/primitives/Tag';
import { StatusDot } from '@/components/primitives/StatusDot';
import { Button } from '@/components/primitives/Button';
import { TabBar } from '@/components/primitives/TabBar';
import { IconButton } from '@/components/primitives/IconButton';
import { SectionHeader } from '@/components/primitives/SectionHeader';
import { Skeleton } from '@/components/primitives/Skeleton';
import { SkeletonCards } from '@/components/primitives/Skeleton';
import { TaskStatus, TaskPriority } from '@/lib/types';

function priorityColor(p: number): 'red' | 'orange' | 'gray' | 'blue' | 'purple' {
  if (p <= 1) return 'red';
  if (p <= 2) return 'orange';
  if (p <= 3) return 'gray';
  if (p <= 4) return 'blue';
  return 'purple';
}

function statusTag(s: string): { label: string; color: 'gray' | 'blue' | 'yellow' | 'green' } {
  switch (s) {
    case 'todo': return { label: 'Todo', color: 'gray' };
    case 'active': return { label: 'Active', color: 'blue' };
    case 'waiting': return { label: 'Waiting', color: 'yellow' };
    case 'done': return { label: 'Done', color: 'green' };
    default: return { label: s, color: 'gray' };
  }
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

const COLUMNS = [
  { id: 'todo', label: 'Todo', filter: (t: Task) => t.status === 'todo' },
  { id: 'active', label: 'Active', filter: (t: Task) => t.status === 'active' },
  { id: 'waiting', label: 'Waiting', filter: (t: Task) => t.status === 'waiting' },
  { id: 'done', label: 'Done', filter: (t: Task) => t.status === 'done' },
];

function KanbanCard({ task, onClick }: { task: Task; onClick: () => void }) {
  return (
    <button onClick={onClick} className="w-full text-left bg-bg-secondary rounded-[7px] p-3 hover:bg-bg-tertiary transition-colors cursor-pointer border border-border" style={{ transitionDuration: 'var(--duration-instant)' }}>
      <p className="text-[13px] font-[510] text-text-secondary mb-2 leading-snug">{task.title}</p>
      <div className="flex items-center gap-2">
        <StatusDot color={priorityColor(task.priority)} size="sm" />
        {task.project && <span className="text-[10px] text-text-quaternary truncate">{task.project}</span>}
        <span className="text-[10px] font-mono text-text-quaternary ml-auto">{task.id}</span>
      </div>
    </button>
  );
}

function InlineCreateForm({ onClose, projects }: { onClose: () => void; projects: string[] }) {
  const [title, setTitle] = useState('');
  const [project, setProject] = useState('');
  const [priority, setPriority] = useState(3);
  const createTask = useCreateTask();
  const handleSubmit = () => {
    if (!title.trim()) return;
    createTask.mutate({ title: title.trim(), project: project || undefined, priority: priority as TaskPriority });
    onClose();
  };
  return (
    <div className="bg-bg-secondary rounded-[7px] p-3 border border-accent/30 mb-2">
      <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); if (e.key === 'Escape') onClose(); }} placeholder="Task title..." autoFocus className="w-full bg-transparent text-[13px] text-text placeholder:text-text-quaternary outline-none mb-2" />
      <div className="flex items-center gap-2 flex-wrap">
        <select value={project} onChange={(e) => setProject(e.target.value)} className="text-[11px] bg-bg-tertiary text-text-secondary rounded-xs px-1.5 py-0.5 border border-border outline-none"><option value="">No project</option>{projects.map((p) => <option key={p} value={p}>{p}</option>)}</select>
        <select value={priority} onChange={(e) => setPriority(Number(e.target.value))} className="text-[11px] bg-bg-tertiary text-text-secondary rounded-xs px-1.5 py-0.5 border border-border outline-none"><option value={1}>P1</option><option value={2}>P2</option><option value={3}>P3</option><option value={4}>P4</option><option value={5}>P5</option></select>
        <div className="ml-auto flex items-center gap-1"><button onClick={onClose} className="text-[11px] text-text-quaternary px-2 py-0.5">Cancel</button><button onClick={handleSubmit} disabled={!title.trim()} className="text-[11px] font-[510] text-accent px-2 py-0.5 disabled:opacity-40">Add</button></div>
      </div>
    </div>
  );
}

function TaskDetailPanel({ task, onClose }: { task: Task; onClose: () => void }) {
  const updateTask = useUpdateTask();
  return (
    <div className="w-full lg:w-[400px] shrink-0 border-t lg:border-t-0 lg:border-l border-border bg-bg-panel overflow-y-auto fixed inset-0 lg:static z-50 lg:z-auto">
      <div className="p-5">
        <div className="flex items-center justify-between mb-4"><span className="text-[10px] font-mono text-text-quaternary">{task.id}</span><IconButton icon={<X />} tooltip="Close" onClick={onClose} /></div>
        <h2 className="text-[17px] font-[650] text-text tracking-[-0.01em] mb-4 leading-snug">{task.title}</h2>
        <div className="space-y-3 mb-6">
          <div className="flex items-center justify-between"><span className="text-[11px] text-text-quaternary">Status</span><select value={task.status} onChange={(e) => updateTask.mutate({ id: task.id, data: { status: e.target.value as TaskStatus } })} className="text-[12px] bg-bg-tertiary text-text-secondary rounded-xs px-2 py-0.5 border border-border outline-none"><option value="todo">Todo</option><option value="active">Active</option><option value="waiting">Waiting</option><option value="done">Done</option></select></div>
          <div className="flex items-center justify-between"><span className="text-[11px] text-text-quaternary">Priority</span><div className="flex items-center gap-1.5"><StatusDot color={priorityColor(task.priority)} size="md" /><span className="text-[12px] text-text-secondary">P{task.priority}</span></div></div>
          {task.project && <div className="flex items-center justify-between"><span className="text-[11px] text-text-quaternary">Project</span><Tag label={task.project} color="blue" /></div>}
          <div className="flex items-center justify-between"><span className="text-[11px] text-text-quaternary">Created</span><span className="text-[12px] text-text-tertiary">{timeAgo(task.created)}</span></div>
        </div>
        {task.tags && task.tags.length > 0 && <div className="mb-6"><SectionHeader label="Tags" /><div className="flex flex-wrap gap-1.5">{task.tags.map((t) => <Tag key={t} label={t} color="gray" />)}</div></div>}
        {task.subtasks && task.subtasks.length > 0 && <div className="mb-6"><SectionHeader label="Subtasks" count={task.subtasks.length} /><div className="space-y-1">{task.subtasks.map((sub) => <div key={sub.id} className="flex items-center gap-2 py-1"><StatusDot color={sub.status === 'done' ? 'green' : 'gray'} size="sm" /><span className={`text-[12px] ${sub.status === 'done' ? 'text-text-quaternary line-through' : 'text-text-secondary'}`}>{sub.title}</span></div>)}</div></div>}
      </div>
    </div>
  );
}

type SortField = 'title' | 'status' | 'priority' | 'project' | 'created';
type SortDir = 'asc' | 'desc';

function sortTasks(tasks: Task[], field: SortField, dir: SortDir): Task[] {
  return [...tasks].sort((a, b) => {
    let cmp = 0;
    switch (field) {
      case 'title': cmp = a.title.localeCompare(b.title); break;
      case 'status': cmp = a.status.localeCompare(b.status); break;
      case 'priority': cmp = a.priority - b.priority; break;
      case 'project': cmp = (a.project || '').localeCompare(b.project || ''); break;
      case 'created': cmp = new Date(a.created).getTime() - new Date(b.created).getTime(); break;
    }
    return dir === 'asc' ? cmp : -cmp;
  });
}

function ListView({ tasks, onSelect }: { tasks: Task[]; onSelect: (t: Task) => void }) {
  const [sortField, setSortField] = useState<SortField>('priority');
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const updateTask = useUpdateTask();
  const sorted = useMemo(() => sortTasks(tasks, sortField, sortDir), [tasks, sortField, sortDir]);
  const toggleSort = (field: SortField) => { if (sortField === field) setSortDir(d => d === 'asc' ? 'desc' : 'asc'); else { setSortField(field); setSortDir('asc'); } };
  const SortHeader = ({ field, label, className = '' }: { field: SortField; label: string; className?: string }) => (
    <button onClick={() => toggleSort(field)} className={`type-overline text-text-quaternary text-left hover:text-text-tertiary transition-colors flex items-center gap-1 ${className}`}>
      {label}{sortField === field && <span className="text-[8px]">{sortDir === 'asc' ? '\u25B2' : '\u25BC'}</span>}
    </button>
  );
  return (
    <div className="overflow-x-auto">
      <div className="flex items-center gap-3 px-3 py-2 border-b border-border-tertiary min-w-[500px]"><div className="w-5" /><SortHeader field="title" label="Title" className="flex-1 min-w-0" /><SortHeader field="status" label="Status" className="w-20 shrink-0" /><SortHeader field="priority" label="Pri" className="w-14 shrink-0" /><SortHeader field="project" label="Project" className="w-24 shrink-0 hidden md:flex" /><SortHeader field="created" label="Created" className="w-20 shrink-0 hidden sm:flex" /></div>
      <div className="min-w-[500px]">{sorted.map(task => {
        const st = statusTag(task.status);
        return (
          <button key={task.id} onClick={() => onSelect(task)} className="w-full flex items-center gap-3 px-3 h-10 hover:bg-hover transition-colors text-left" style={{ transitionDuration: 'var(--duration-instant)' }}>
            <div className="w-5 flex items-center justify-center"><input type="checkbox" checked={task.status === 'done'} onChange={(e) => { e.stopPropagation(); updateTask.mutate({ id: task.id, data: { status: e.target.checked ? TaskStatus.DONE : TaskStatus.TODO } }); }} onClick={(e) => e.stopPropagation()} className="w-3.5 h-3.5 accent-accent" /></div>
            <span className={`flex-1 min-w-0 text-[13px] font-[510] truncate ${task.status === 'done' ? 'text-text-quaternary line-through' : 'text-text-secondary'}`}>{task.title}</span>
            <div className="w-20 shrink-0"><Tag label={st.label} color={st.color} /></div>
            <div className="w-14 shrink-0 flex items-center gap-1"><StatusDot color={priorityColor(task.priority)} size="sm" /><span className="text-[11px] text-text-tertiary">P{task.priority}</span></div>
            <span className="w-24 shrink-0 text-[11px] text-text-quaternary truncate hidden md:block">{task.project || '\u2014'}</span>
            <span className="w-20 shrink-0 text-[10px] text-text-quaternary hidden sm:block">{timeAgo(task.created)}</span>
          </button>
        );
      })}</div>
    </div>
  );
}

export default function TasksPage() {
  const { data, isLoading, isError } = useWork();
  const [view, setView] = useState<'kanban' | 'list'>('kanban');
  const [creating, setCreating] = useState(false);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [filterProject, setFilterProject] = useState('');
  const tasks = data?.tasks ?? [];
  const projects = useMemo(() => [...new Set(tasks.map(t => t.project).filter(Boolean) as string[])], [tasks]);
  const filteredTasks = useMemo(() => filterProject ? tasks.filter(t => t.project === filterProject) : tasks, [tasks, filterProject]);

  if (isLoading) return <div className="px-5 md:px-8 py-6"><Skeleton className="h-7 w-32 mb-6" /><SkeletonCards count={6} /></div>;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="shrink-0 px-5 md:px-8 py-4 md:py-6 border-b border-border">
        <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
          <h1 className="type-title">Tasks</h1>
          <div className="flex items-center gap-2">
            <TabBar tabs={[{ id: 'kanban', label: 'Board' }, { id: 'list', label: 'List' }]} active={view} onChange={(id) => setView(id as 'kanban' | 'list')} />
            <Button variant="primary" size="sm" icon={<Plus />} onClick={() => setCreating(true)}><span className="hidden sm:inline">New</span></Button>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <select value={filterProject} onChange={(e) => setFilterProject(e.target.value)} className="text-[11px] bg-bg-secondary text-text-secondary rounded-sm px-2 py-1 border border-border outline-none h-7"><option value="">All projects</option>{projects.map((p) => <option key={p} value={p}>{p}</option>)}</select>
          {filterProject && <button onClick={() => setFilterProject('')} className="text-[10px] text-text-quaternary hover:text-text-secondary flex items-center gap-1"><X className="w-3 h-3" />Clear</button>}
        </div>
      </div>
      {isError && <div className="px-5 md:px-8 pt-4"><ErrorBanner /></div>}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        <div className="flex-1 overflow-auto">
          {filteredTasks.length === 0 && !creating ? (
            <EmptyState icon={<CheckSquare />} title="No tasks yet" description="Create your first task." action={<Button variant="primary" size="sm" icon={<Plus />} onClick={() => setCreating(true)}>New Task</Button>} />
          ) : view === 'kanban' ? (
            <div className="flex gap-4 p-5 md:p-8 overflow-x-auto min-h-full">
              {COLUMNS.map((col) => {
                const colTasks = filteredTasks.filter(col.filter);
                const display = col.id === 'done' ? colTasks.slice(-15).reverse() : colTasks;
                return (
                  <div key={col.id} className="flex-1 min-w-[240px] max-w-[350px] flex flex-col">
                    <div className="flex items-center justify-between mb-3"><span className="type-overline text-text-quaternary">{col.label}</span><span className="text-[10px] text-text-quaternary bg-bg-tertiary rounded-xs px-1.5 py-0.5">{colTasks.length}</span></div>
                    {creating && col.id === 'todo' && <InlineCreateForm onClose={() => setCreating(false)} projects={projects} />}
                    <div className="space-y-1.5 flex-1 overflow-y-auto">{display.length === 0 && <p className="text-[11px] text-text-quaternary py-4 text-center">No tasks</p>}{display.map((task) => <KanbanCard key={task.id} task={task} onClick={() => setSelectedTask(task)} />)}</div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="p-5 md:p-8">{creating && <InlineCreateForm onClose={() => setCreating(false)} projects={projects} />}<ListView tasks={filteredTasks} onSelect={setSelectedTask} /></div>
          )}
        </div>
        {selectedTask && <TaskDetailPanel task={selectedTask} onClose={() => setSelectedTask(null)} />}
      </div>
    </div>
  );
}
