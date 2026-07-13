/**
 * useTaskKeyboard — keyboard navigation for the Tasks view.
 *
 * Shortcuts (active when no input is focused):
 *   j / ArrowDown    — move focus to next task
 *   k / ArrowUp      — move focus to previous task
 *   Enter            — open focused task in detail panel
 *   Space / x        — toggle done on focused task
 *   1-5              — set priority on focused task
 *   /                — focus search input
 *   n                — new task
 *   Escape           — close detail / deselect / close search
 *   d                — mark focused task done
 *   a                — mark focused task active
 */

import { useEffect, useCallback, useRef } from 'react';
import type { Task } from '@/hooks/useWork';

interface UseTaskKeyboardOptions {
  tasks: Task[];
  focusedId: string | null;
  selectedId: string | null;
  onFocus: (id: string | null) => void;
  onSelect: (task: Task | null) => void;
  onToggleDone: (task: Task) => void;
  onSetPriority: (task: Task, priority: number) => void;
  onSetStatus: (task: Task, status: string) => void;
  onStartCreate: () => void;
  onFocusSearch: () => void;
}

export function useTaskKeyboard({
  tasks,
  focusedId,
  selectedId,
  onFocus,
  onSelect,
  onToggleDone,
  onSetPriority,
  onSetStatus,
  onStartCreate,
  onFocusSearch,
}: UseTaskKeyboardOptions) {
  // Stable refs for callbacks to avoid re-registering listener
  const optsRef = useRef({
    tasks, focusedId, selectedId, onFocus, onSelect,
    onToggleDone, onSetPriority, onSetStatus, onStartCreate, onFocusSearch,
  });
  optsRef.current = {
    tasks, focusedId, selectedId, onFocus, onSelect,
    onToggleDone, onSetPriority, onSetStatus, onStartCreate, onFocusSearch,
  };

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      const {
        tasks, focusedId, selectedId, onFocus, onSelect,
        onToggleDone, onSetPriority, onSetStatus, onStartCreate, onFocusSearch,
      } = optsRef.current;

      const focusedTask = focusedId ? tasks.find(t => t.id === focusedId) : null;
      const currentIdx = focusedId ? tasks.findIndex(t => t.id === focusedId) : -1;

      switch (e.key) {
        // Navigation
        case 'j':
        case 'ArrowDown': {
          e.preventDefault();
          const next = currentIdx < tasks.length - 1 ? currentIdx + 1 : 0;
          onFocus(tasks[next]?.id ?? null);
          break;
        }
        case 'k':
        case 'ArrowUp': {
          e.preventDefault();
          const prev = currentIdx > 0 ? currentIdx - 1 : tasks.length - 1;
          onFocus(tasks[prev]?.id ?? null);
          break;
        }

        // Open detail
        case 'Enter': {
          if (focusedTask) {
            e.preventDefault();
            onSelect(focusedTask);
          }
          break;
        }

        // Toggle done
        case ' ':
        case 'x': {
          if (focusedTask) {
            e.preventDefault();
            onToggleDone(focusedTask);
          }
          break;
        }

        // Priority
        case '1': case '2': case '3': case '4': case '5': {
          if (focusedTask) {
            e.preventDefault();
            onSetPriority(focusedTask, parseInt(e.key));
          }
          break;
        }

        // Status shortcuts
        case 'd': {
          if (focusedTask) {
            e.preventDefault();
            onSetStatus(focusedTask, 'done');
          }
          break;
        }
        case 'a': {
          if (focusedTask) {
            e.preventDefault();
            onSetStatus(focusedTask, 'active');
          }
          break;
        }
        case 't': {
          if (focusedTask) {
            e.preventDefault();
            onSetStatus(focusedTask, 'todo');
          }
          break;
        }

        // Search
        case '/': {
          e.preventDefault();
          onFocusSearch();
          break;
        }

        // New task
        case 'n': {
          e.preventDefault();
          onStartCreate();
          break;
        }

        // Escape — close detail, then deselect focus
        case 'Escape': {
          e.preventDefault();
          if (selectedId) {
            onSelect(null);
          } else if (focusedId) {
            onFocus(null);
          }
          break;
        }
      }
    };

    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []); // Empty deps — we use refs

  // Scroll focused task into view
  useEffect(() => {
    if (!focusedId) return;
    const el = document.querySelector(`[data-task-id="${focusedId}"]`);
    if (el) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [focusedId]);
}
