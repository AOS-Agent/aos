/**
 * TaskRow — one task line inside a phase or status group.
 *
 * Extracted from ProjectDetail so phases, status groups and the fallback list
 * all render the same row. Click opens the shared task peek; the circle toggles
 * done without opening anything.
 */

import { useTaskOverlay } from '@/components/tasks/TaskOverlayContext';
import type { Task } from '@/hooks/useWork';

export function TaskRow({ task, dot, onToggle }: { task: Task; dot: string; onToggle: () => void }) {
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
        style={{
          borderColor: done ? 'var(--color-green)' : 'var(--color-border-secondary)',
          backgroundColor: done ? 'var(--color-green)' : 'transparent',
        }}
      >
        {done && (
          <svg width="8" height="6" viewBox="0 0 10 8" fill="none">
            <path d="M1 4L3.5 6.5L9 1" stroke="var(--color-on-accent)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </button>
      <span className={`w-[6px] h-[6px] rounded-full shrink-0 ${dot}`} />
      <span className={`flex-1 min-w-0 text-[15px] truncate ${done ? 'text-text-quaternary line-through' : 'text-text-secondary'}`}>
        {task.title}
      </span>
      {task.status === 'active' && (
        <span className="text-[10px] font-[510] uppercase tracking-[0.06em] text-tag-green shrink-0">Active</span>
      )}
      {task.status === 'waiting' && (
        <span className="text-[10px] font-[510] uppercase tracking-[0.06em] text-tag-yellow shrink-0">Waiting</span>
      )}
    </div>
  );
}
