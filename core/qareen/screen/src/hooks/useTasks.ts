import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { CreateTaskRequest, UpdateTaskRequest } from '@/lib/types';
import type { Task } from '@/hooks/useWork';

const API = '/api';

/**
 * Full task list for a single project — NOT capped (the global /api/work caps at
 * 200, which starves large projects). Used by ProjectDetail so the visible list
 * matches the authoritative counts.
 */
export function useProjectTasks(projectId: string | null | undefined) {
  return useQuery({
    queryKey: ['project-tasks', projectId],
    enabled: !!projectId,
    staleTime: 30_000,
    queryFn: async (): Promise<Task[]> => {
      const res = await fetch(`${API}/tasks?project=${encodeURIComponent(projectId!)}&limit=2000`);
      if (!res.ok) throw new Error(`Project tasks failed: ${res.status}`);
      const d = await res.json();
      return Array.isArray(d.tasks) ? d.tasks : [];
    },
  });
}

export function useCreateTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: CreateTaskRequest) => {
      const res = await fetch(`${API}/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error(`Create task failed: ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['work'] });
      qc.invalidateQueries({ queryKey: ['project-tasks'] });
    },
  });
}

export function useUpdateTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, data }: { id: string; data: UpdateTaskRequest }) => {
      const res = await fetch(`${API}/tasks/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error(`Update task failed: ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['work'] });
      qc.invalidateQueries({ queryKey: ['project-tasks'] });
    },
  });
}

export function useDeleteTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${API}/tasks/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`Delete task failed: ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['work'] });
      qc.invalidateQueries({ queryKey: ['project-tasks'] });
    },
  });
}
