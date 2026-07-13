/**
 * TaskOverlayContext — shared "peek" controller for tasks.
 *
 * Any row anywhere in the Work section can call `useTaskOverlay().openTask(id)`
 * to float a centered Notion-style peek over the current view. The provider owns
 * the open task id and renders <TaskOverlay> above its children, so the peek is
 * independent of Work's tab/history/detail logic.
 */

import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';
import { TaskOverlay } from './TaskOverlay';

interface TaskOverlayValue {
  openTask: (taskId: string) => void;
  closeTask: () => void;
}

const TaskOverlayContext = createContext<TaskOverlayValue>({
  openTask: () => {},
  closeTask: () => {},
});

export function TaskOverlayProvider({ children }: { children: ReactNode }) {
  const [openTaskId, setOpenTaskId] = useState<string | null>(null);

  const openTask = useCallback((taskId: string) => setOpenTaskId(taskId), []);
  const closeTask = useCallback(() => setOpenTaskId(null), []);

  return (
    <TaskOverlayContext.Provider value={{ openTask, closeTask }}>
      {children}
      {openTaskId && <TaskOverlay taskId={openTaskId} onClose={closeTask} />}
    </TaskOverlayContext.Provider>
  );
}

/** Open the shared task peek from any row. */
export function useTaskOverlay() {
  const { openTask } = useContext(TaskOverlayContext);
  return { openTask };
}
