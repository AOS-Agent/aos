'use client';

import { Loader2 } from 'lucide-react';
import { useWork, type Task } from '@/hooks/useWork';

function TaskCard({ task }: { task: Task }) {
  return (
    <div
      className="bg-bg-secondary rounded-[5px] p-3 hover:bg-bg-tertiary transition-colors cursor-pointer border border-border"
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      <p className="text-[13px] font-[510] text-text-secondary mb-2 leading-snug">{task.title}</p>
      <div className="flex items-center gap-2">
        <span className="text-[10px] text-text-quaternary">{task.project || '—'}</span>
        <span className={`w-1.5 h-1.5 rounded-full ${task.priority <= 1 ? 'bg-red' : task.priority <= 2 ? 'bg-text-tertiary' : 'bg-text-quaternary'}`} />
        <span className="text-[10px] text-text-quaternary ml-auto">{task.id}</span>
      </div>
    </div>
  );
}

const COLUMNS = [
  { id: 'todo', label: 'Backlog', filter: (t: Task) => t.status === 'todo' },
  { id: 'active', label: 'In Progress', filter: (t: Task) => t.status === 'active' || t.status === 'waiting' },
  { id: 'done', label: 'Done', filter: (t: Task) => t.status === 'done' },
];

export default function TasksPage() {
  const { data, isLoading } = useWork();
  const tasks = data?.tasks ?? [];

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-text-quaternary py-8">
        <Loader2 className="w-4 h-4 animate-spin" />
        <span className="text-[13px]">Loading tasks...</span>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <h1 className="text-[22px] font-[680] text-text tracking-[-0.025em] mb-6">Tasks</h1>
      <div className="flex-1 flex gap-4 overflow-x-auto pb-4">
        {COLUMNS.map((col) => {
          const colTasks = tasks.filter(col.filter);
          // For "Done" column, only show recent 10
          const displayTasks = col.id === 'done' ? colTasks.slice(-10).reverse() : colTasks;

          return (
            <div key={col.id} className="flex-1 min-w-[260px] flex flex-col">
              <div className="flex items-center justify-between mb-3">
                <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">
                  {col.label}
                </span>
                <span className="text-[10px] text-text-quaternary">{colTasks.length}</span>
              </div>

              <div className="space-y-1.5 flex-1 overflow-y-auto">
                {displayTasks.length === 0 ? (
                  <p className="text-[11px] text-text-quaternary py-4 text-center">No tasks</p>
                ) : (
                  displayTasks.map((task) => (
                    <TaskCard key={task.id} task={task} />
                  ))
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
