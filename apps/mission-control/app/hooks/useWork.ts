'use client';

import { useQuery } from '@tanstack/react-query';

const API = 'http://localhost:4096/api';

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
  return res.json();
}

export function useWork() {
  return useQuery({
    queryKey: ['work'],
    queryFn: fetchWork,
    refetchInterval: 120_000,  // SSE handles real-time; this is a fallback
  });
}

export function useActiveTasks() {
  const { data, ...rest } = useWork();
  const tasks = data?.tasks.filter(t => t.status === 'active' || t.status === 'waiting') ?? [];
  return { tasks, ...rest };
}

export function useTodoTasks() {
  const { data, ...rest } = useWork();
  const tasks = data?.tasks.filter(t => t.status === 'todo') ?? [];
  return { tasks, ...rest };
}

export function useTasksByStatus() {
  const { data, ...rest } = useWork();
  const all = data?.tasks ?? [];
  return {
    backlog: all.filter(t => t.status === 'todo'),
    active: all.filter(t => t.status === 'active' || t.status === 'waiting'),
    done: all.filter(t => t.status === 'done').slice(-10),
    ...rest,
  };
}

export function useInbox() {
  const { data, ...rest } = useWork();
  return { inbox: data?.inbox ?? [], ...rest };
}

export function useProjects() {
  const { data, ...rest } = useWork();
  return { projects: data?.projects ?? [], ...rest };
}
