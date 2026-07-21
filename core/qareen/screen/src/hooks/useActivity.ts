import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

const API = '/api';

/** One entry in a task's narrative timeline (Kanban Phase 2). */
export interface ActivityEntry {
  type: 'activity';
  id?: number | string;
  kind:
    | 'created' | 'status_changed' | 'delegated' | 'held'
    | 'comment' | 'attempt' | 'proof' | 'blocked' | 'unblocked'
    | 'edited' | 'linked';
  body: string;
  data?: Record<string, unknown>;
  actor: string;
  actor_type: 'operator' | 'agent' | 'system';
  timestamp: string;
  source_event_id?: string | null;
}

/**
 * A task's narrative activity — typed events with a human body and an
 * expandable data payload. Oldest-first from the API; the timeline renders it
 * newest-first. Live-invalidated by useSSE on any task.* / task.activity event.
 */
export function useTaskActivity(taskId: string | null | undefined) {
  return useQuery({
    queryKey: ['activity', taskId],
    enabled: !!taskId,
    staleTime: 10_000,
    queryFn: async (): Promise<ActivityEntry[]> => {
      const res = await fetch(`${API}/tasks/${taskId}/activity`);
      if (!res.ok) throw new Error(`Activity failed: ${res.status}`);
      const d = await res.json();
      return Array.isArray(d.activity) ? d.activity : [];
    },
  });
}

/** Append a narrative entry (comment / attempt / proof / blocked / …). */
export function useAppendActivity(taskId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (entry: { kind: string; body: string; data?: unknown; actor?: string }) => {
      const res = await fetch(`${API}/tasks/${taskId}/activity`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(entry),
      });
      if (!res.ok) throw new Error(`Append activity failed: ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['activity', taskId] });
      qc.invalidateQueries({ queryKey: ['work'] });
    },
  });
}
