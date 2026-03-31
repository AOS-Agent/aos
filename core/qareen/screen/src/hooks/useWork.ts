import { useQuery } from '@tanstack/react-query';

const API = '/api';

export interface Task {
  id: string;
  title: string;
  status: 'todo' | 'active' | 'waiting' | 'done' | 'cancelled';
  priority: number;
  project: string | null;
  created: string;
  completed: string | null;
  tags: string[];
  subtasks?: Task[];
  source?: string;
}

export interface InboxItem {
  id: string;
  text: string;
  captured: string;
  source: string;
}

export interface Project {
  id: string;
  title: string;
  status: string;
  goal?: string;
}

export interface WorkData {
  tasks: Task[];
  projects: Project[];
  goals: unknown[];
  threads: unknown[];
  inbox: InboxItem[];
  summary: {
    total_tasks: number;
    by_status: Record<string, number>;
    by_priority: Record<string, number>;
    projects: number;
    goals: number;
    threads: number;
    inbox: number;
  };
}

async function fetchWork(): Promise<WorkData> {
  const res = await fetch(`${API}/work`);
  if (!res.ok) throw new Error(`Work API error: ${res.status}`);
  const raw = await res.json();
  return {
    tasks: Array.isArray(raw.tasks) ? raw.tasks : (raw.tasks?.tasks ?? []),
    projects: Array.isArray(raw.projects) ? raw.projects : (raw.projects?.projects ?? []),
    goals: Array.isArray(raw.goals) ? raw.goals : (raw.goals?.goals ?? []),
    threads: Array.isArray(raw.threads) ? raw.threads : (raw.threads?.threads ?? []),
    inbox: Array.isArray(raw.inbox) ? raw.inbox : (raw.inbox ?? []),
    summary: raw.summary ?? raw.stats ?? {},
  } as unknown as WorkData;
}

export function useWork() {
  return useQuery({
    queryKey: ['work'],
    queryFn: fetchWork,
    staleTime: 30_000,
    refetchInterval: 120_000,
  });
}

export function useActiveTasks() {
  const { data, ...rest } = useWork();
  const all = Array.isArray(data?.tasks) ? data.tasks : [];
  const tasks = all.filter(t => t.status === 'active' || t.status === 'waiting');
  return { tasks, ...rest };
}

export function useTodoTasks() {
  const { data, ...rest } = useWork();
  const all = Array.isArray(data?.tasks) ? data.tasks : [];
  const tasks = all.filter(t => t.status === 'todo');
  return { tasks, ...rest };
}

export function useTasksByStatus() {
  const { data, ...rest } = useWork();
  const all = Array.isArray(data?.tasks) ? data.tasks : [];
  return {
    backlog: all.filter(t => t.status === 'todo'),
    active: all.filter(t => t.status === 'active' || t.status === 'waiting'),
    done: all.filter(t => t.status === 'done').slice(-10),
    ...rest,
  };
}

export function useInbox() {
  const { data, ...rest } = useWork();
  return { inbox: Array.isArray(data?.inbox) ? data.inbox : [], ...rest };
}

export function useProjects() {
  const { data, ...rest } = useWork();
  return { projects: Array.isArray(data?.projects) ? data.projects : [], ...rest };
}
