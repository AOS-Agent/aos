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
import { SkeletonCards } from '@/components/primitives/Skeleton';
import { TaskStatus, TaskPriority } from '@/lib/types';

function priorityColor(p: number): 'red' | 'orange' | 'gray' | 'blue' | 'purple' {
  if (p <= 1) return 'red';
  if (p <= 2) return 'orange';
  if (p <= 3) return 'gray';
  if (p <= 4) return 'blue';
  return 'purple';
}

function priorityLabel(p: number): string {
  if (p <= 1) return 'Urgent';
  if (p <= 2) return 'High';
  if (p <= 3) return 'Normal';
  if (p <= 4) return 'Low';
  return 'Lowest';
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

const columnAccent: Record<string, string> = {
  todo: 'bg-text-quaternary',
  active: 'bg-blue',
  waiting: 'bg-yellow',
  done: 'bg-green',
};

function KanbanCard({ task, onClick }: { task: Task; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="w-full text-left bg-bg-secondary rounded-[7px] p-3.5 hover:bg-bg-tertiary transition-colors cursor-pointer border border-border-secondary hover:border-border-tertiary group"
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      <div className="flex items-start gap-2 mb-2.5">
        <StatusDot color={priorityColor(task.priority)} size="sm" className="mt-1.5 shrink-0" />
        <p className="text-[13px] font-[510] text-text-secondary leading-[1.45] group-hover:text-text transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
          {task.title}
        </p>
      </div>
      <div className="flex items-center gap-2">
        {task.project && (
          <span className="text-[10px] font-[510] text-text-quaternary bg-bg-tertiary rounded-xs px-1.5 py-0.5 truncate max-w-[120px]">
            {task.project}
          </span>
        )}
        <span className="text-[10px] font-mono text-text-quaternary ml-auto opacity-60 group-hover:opacity-100 transition-opacity" style={{ transitionDuration: 'var(--duration-instant)' }}>
          {task.id}
        </span>
      </div>
      {task.subtasks && task.subtasks.length > 0 && (
        <div className="flex items-center gap-1.5 mt-2.5 pt-2.5 border-t border-border">
          <span className="text-[10px] text-text-quaternary">
            {task.subtasks.filter(s => s.status === 'done').length}/{task.subtasks.length} subtasks
          </span>
          <div className="flex-1 h-1 bg-bg-tertiary rounded-full overflow-hidden">
            <div
              className="h-full bg-accent rounded-full transition-all"
              style={{ width: `${(task.subtasks.filter(s => s.status === 'done').length / task.subtasks.length) * 100}%` }}
            />
          </div>
        </div>
      )}
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
    <div className="bg-bg-secondary rounded-[7px] p-4 border border-accent/30 mb-3 shadow-[0_0_0_1px_rgba(217,115,13,0.08)]">
      <input
        type="text"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); if (e.key === 'Escape') onClose(); }}
        placeholder="What needs to be done?"
        autoFocus
        className="w-full bg-transparent text-[14px] text-text placeholder:text-text-quaternary outline-none mb-3"
        style={{ fontFamily: 'var(--font-serif)' }}
      />
      <div className="flex items-center gap-2 flex-wrap">
        <select
          value={project}
          onChange={(e) => setProject(e.target.value)}
          className="text-[11px] bg-bg-tertiary text-text-secondary rounded-xs px-2 py-1 border border-border outline-none cursor-pointer hover:border-border-secondary transition-colors"
          style={{ transitionDuration: 'var(--duration-instant)' }}
        >
          <option value="">No project</option>
          {projects.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <select
          value={priority}
          onChange={(e) => setPriority(Number(e.target.value))}
          className="text-[11px] bg-bg-tertiary text-text-secondary rounded-xs px-2 py-1 border border-border outline-none cursor-pointer hover:border-border-secondary transition-colors"
          style={{ transitionDuration: 'var(--duration-instant)' }}
        >
          <option value={1}>P1 — Urgent</option>
          <option value={2}>P2 — High</option>
          <option value={3}>P3 — Normal</option>
          <option value={4}>P4 — Low</option>
          <option value={5}>P5 — Lowest</option>
        </select>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={onClose}
            className="text-[12px] text-text-quaternary hover:text-text-secondary px-2 py-1 rounded-xs hover:bg-hover transition-colors cursor-pointer"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!title.trim()}
            className="text-[12px] font-[510] text-bg bg-accent hover:bg-accent-hover px-3 py-1 rounded-xs disabled:opacity-40 transition-colors cursor-pointer"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            Add task
          </button>
        </div>
      </div>
    </div>
  );
}

function TaskDetailPanel({ task, onClose }: { task: Task; onClose: () => void }) {
  const updateTask = useUpdateTask();
  return (
    <div className="w-full lg:w-[420px] shrink-0 border-t lg:border-t-0 lg:border-l border-border bg-bg-panel overflow-y-auto fixed inset-0 lg:static z-50 lg:z-auto">
      <div className="p-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <span className="text-[10px] font-mono text-text-quaternary tracking-wider">{task.id}</span>
          <IconButton icon={<X />} tooltip="Close" onClick={onClose} />
        </div>

        {/* Title */}
        <h2 className="text-[18px] font-[600] text-text tracking-[-0.015em] mb-6 leading-[1.35]" style={{ fontFamily: 'var(--font-serif)' }}>
          {task.title}
        </h2>

        {/* Properties */}
        <div className="space-y-4 mb-8">
          <div className="flex items-center justify-between py-1">
            <span className="text-[11px] font-[510] text-text-quaternary uppercase tracking-[0.04em]">Status</span>
            <select
              value={task.status}
              onChange={(e) => updateTask.mutate({ id: task.id, data: { status: e.target.value as TaskStatus } })}
              className="text-[12px] bg-bg-tertiary text-text-secondary rounded-xs px-2.5 py-1 border border-border outline-none cursor-pointer hover:border-border-secondary transition-colors"
              style={{ transitionDuration: 'var(--duration-instant)' }}
            >
              <option value="todo">Todo</option>
              <option value="active">Active</option>
              <option value="waiting">Waiting</option>
              <option value="done">Done</option>
            </select>
          </div>
          <div className="flex items-center justify-between py-1">
            <span className="text-[11px] font-[510] text-text-quaternary uppercase tracking-[0.04em]">Priority</span>
            <div className="flex items-center gap-2">
              <StatusDot color={priorityColor(task.priority)} size="md" />
              <span className="text-[12px] text-text-secondary font-[510]">P{task.priority}</span>
              <span className="text-[11px] text-text-tertiary">{priorityLabel(task.priority)}</span>
            </div>
          </div>
          {task.project && (
            <div className="flex items-center justify-between py-1">
              <span className="text-[11px] font-[510] text-text-quaternary uppercase tracking-[0.04em]">Project</span>
              <Tag label={task.project} color="blue" />
            </div>
          )}
          <div className="flex items-center justify-between py-1">
            <span className="text-[11px] font-[510] text-text-quaternary uppercase tracking-[0.04em]">Created</span>
            <span className="text-[12px] text-text-tertiary">{timeAgo(task.created)}</span>
          </div>
        </div>

        {/* Tags */}
        {task.tags && task.tags.length > 0 && (
          <div className="mb-8">
            <SectionHeader label="Tags" />
            <div className="flex flex-wrap gap-1.5">
              {task.tags.map((t) => <Tag key={t} label={t} color="gray" />)}
            </div>
          </div>
        )}

        {/* Subtasks */}
        {task.subtasks && task.subtasks.length > 0 && (
          <div className="mb-8">
            <SectionHeader label="Subtasks" count={task.subtasks.length} />
            <div className="space-y-1">
              {task.subtasks.map((sub) => (
                <div
                  key={sub.id}
                  className="flex items-center gap-2.5 py-1.5 px-2 rounded-xs hover:bg-hover transition-colors"
                  style={{ transitionDuration: 'var(--duration-instant)' }}
                >
                  <StatusDot color={sub.status === 'done' ? 'green' : 'gray'} size="sm" />
                  <span
                    className={`text-[13px] leading-[1.5] ${sub.status === 'done' ? 'text-text-quaternary line-through' : 'text-text-secondary'}`}
                    style={{ fontFamily: 'var(--font-serif)' }}
                  >
                    {sub.title}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
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
  const toggleSort = (field: SortField) => {
    if (sortField === field) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortField(field); setSortDir('asc'); }
  };

  const SortHeader = ({ field, label, className = '' }: { field: SortField; label: string; className?: string }) => (
    <button
      onClick={() => toggleSort(field)}
      className={`text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary text-left hover:text-text-tertiary transition-colors flex items-center gap-1 cursor-pointer ${className}`}
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      {label}
      {sortField === field && <span className="text-[8px] text-accent">{sortDir === 'asc' ? '\u25B2' : '\u25BC'}</span>}
    </button>
  );

  return (
    <div className="overflow-x-auto">
      {/* Table header */}
      <div className="flex items-center gap-3 px-3 py-2.5 border-b border-border-secondary">
        <div className="w-5" />
        <SortHeader field="title" label="Title" className="flex-1 min-w-0" />
        <SortHeader field="status" label="Status" className="w-20 shrink-0" />
        <SortHeader field="priority" label="Priority" className="w-16 shrink-0" />
        <SortHeader field="project" label="Project" className="w-20 shrink-0 hidden md:flex" />
        <SortHeader field="created" label="Created" className="w-16 shrink-0 hidden sm:flex" />
      </div>

      {/* Table body */}
      <div>
        {sorted.map(task => {
          const st = statusTag(task.status);
          return (
            <button
              key={task.id}
              onClick={() => onSelect(task)}
              className="w-full flex items-center gap-3 px-3 h-11 hover:bg-hover transition-colors text-left cursor-pointer border-b border-border/50 last:border-b-0"
              style={{ transitionDuration: 'var(--duration-instant)' }}
            >
              <div className="w-5 flex items-center justify-center">
                <input
                  type="checkbox"
                  checked={task.status === 'done'}
                  onChange={(e) => { e.stopPropagation(); updateTask.mutate({ id: task.id, data: { status: e.target.checked ? TaskStatus.DONE : TaskStatus.TODO } }); }}
                  onClick={(e) => e.stopPropagation()}
                  className="w-3.5 h-3.5 accent-accent cursor-pointer"
                />
              </div>
              <span
                className={`flex-1 min-w-0 text-[13px] font-[510] truncate ${task.status === 'done' ? 'text-text-quaternary line-through' : 'text-text-secondary'}`}
              >
                {task.title}
              </span>
              <div className="w-20 shrink-0">
                <Tag label={st.label} color={st.color} />
              </div>
              <div className="w-16 shrink-0 flex items-center gap-1.5">
                <StatusDot color={priorityColor(task.priority)} size="sm" />
                <span className="text-[11px] text-text-tertiary font-[510]">P{task.priority}</span>
              </div>
              <span className="w-20 shrink-0 text-[11px] text-text-quaternary truncate hidden md:block">{task.project || '\u2014'}</span>
              <span className="w-16 shrink-0 text-[10px] text-text-quaternary hidden sm:block">{timeAgo(task.created)}</span>
            </button>
          );
        })}
      </div>
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

  // Summary counts
  const todoCount = filteredTasks.filter(t => t.status === 'todo').length;
  const activeCount = filteredTasks.filter(t => t.status === 'active').length;
  const doneCount = filteredTasks.filter(t => t.status === 'done').length;

  if (isLoading) return <div className="px-6 py-6"><SkeletonCards count={6} /></div>;

  return (
    <div className="flex flex-col h-full overflow-hidden bg-bg">
      {/* Header bar */}
      <div className="shrink-0 px-6 md:px-8 pt-14 pb-4 border-b border-border">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-4">
            <h1 className="text-[22px] font-[680] text-text tracking-[-0.025em] leading-none">Tasks</h1>
            <span className="text-[11px] text-text-quaternary font-mono">{filteredTasks.length}</span>
          </div>
          <Button variant="primary" size="sm" icon={<Plus />} onClick={() => setCreating(true)}>New task</Button>
        </div>

        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-3">
            <TabBar
              tabs={[{ id: 'kanban', label: 'Board' }, { id: 'list', label: 'List' }]}
              active={view}
              onChange={(id) => setView(id as 'kanban' | 'list')}
            />
            <div className="h-4 w-px bg-border-secondary" />
            <select
              value={filterProject}
              onChange={(e) => setFilterProject(e.target.value)}
              className="text-[11px] bg-bg-secondary text-text-secondary rounded-sm px-2.5 py-1.5 border border-border outline-none h-7 cursor-pointer hover:border-border-secondary transition-colors"
              style={{ transitionDuration: 'var(--duration-instant)' }}
            >
              <option value="">All projects</option>
              {projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
            {filterProject && (
              <button
                onClick={() => setFilterProject('')}
                className="text-[10px] text-text-quaternary hover:text-text-secondary flex items-center gap-1 cursor-pointer transition-colors"
                style={{ transitionDuration: 'var(--duration-instant)' }}
              >
                <X className="w-3 h-3" />
                Clear
              </button>
            )}
          </div>

          {/* Summary pills */}
          <div className="flex items-center gap-3 text-[10px] text-text-quaternary">
            <span className="flex items-center gap-1.5"><StatusDot color="gray" size="sm" />{todoCount} todo</span>
            <span className="flex items-center gap-1.5"><StatusDot color="blue" size="sm" />{activeCount} active</span>
            <span className="flex items-center gap-1.5"><StatusDot color="green" size="sm" />{doneCount} done</span>
          </div>
        </div>
      </div>

      {isError && <div className="px-6 md:px-8 pt-4"><ErrorBanner /></div>}

      {/* Content area */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        <div className={`flex-1 ${view === 'kanban' ? 'overflow-hidden' : 'overflow-auto'}`}>
          {filteredTasks.length === 0 && !creating ? (
            <EmptyState
              icon={<CheckSquare />}
              title="No tasks yet"
              description="Create your first task to start tracking your work."
              action={<Button variant="primary" size="sm" icon={<Plus />} onClick={() => setCreating(true)}>New task</Button>}
            />
          ) : view === 'kanban' ? (
            <div className="flex gap-4 p-5 md:p-6 overflow-x-auto h-full snap-x snap-mandatory sm:snap-none">
              {COLUMNS.map((col) => {
                const colTasks = filteredTasks.filter(col.filter);
                const display = col.id === 'done' ? colTasks.slice(-15).reverse() : colTasks;
                return (
                  <div key={col.id} className="flex-1 min-w-[220px] sm:min-w-[260px] max-w-[360px] flex flex-col snap-start min-h-0">
                    {/* Column header */}
                    <div className="flex items-center gap-2.5 mb-3 shrink-0 px-1">
                      <div className={`w-2 h-2 rounded-full ${columnAccent[col.id]}`} />
                      <span className="text-[11px] font-[590] text-text-tertiary tracking-[-0.005em]">{col.label}</span>
                      <span className="text-[10px] text-text-quaternary bg-bg-tertiary rounded-xs px-1.5 py-0.5 font-mono">{colTasks.length}</span>
                    </div>

                    {creating && col.id === 'todo' && <InlineCreateForm onClose={() => setCreating(false)} projects={projects} />}

                    <div className="space-y-2 flex-1 overflow-y-auto min-h-0 pr-1">
                      {display.length === 0 && (
                        <p className="text-[12px] text-text-quaternary py-8 text-center" style={{ fontFamily: 'var(--font-serif)' }}>
                          {col.id === 'todo' ? 'Nothing to do right now' : col.id === 'active' ? 'No tasks in progress' : col.id === 'waiting' ? 'Nothing blocked' : 'No completed tasks'}
                        </p>
                      )}
                      {display.map((task) => <KanbanCard key={task.id} task={task} onClick={() => setSelectedTask(task)} />)}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="p-6 md:p-8">
              {creating && <InlineCreateForm onClose={() => setCreating(false)} projects={projects} />}
              <ListView tasks={filteredTasks} onSelect={setSelectedTask} />
            </div>
          )}
        </div>
        {selectedTask && <TaskDetailPanel task={selectedTask} onClose={() => setSelectedTask(null)} />}
      </div>
    </div>
  );
}
