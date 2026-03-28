// ---------------------------------------------------------------------------
// HTTP client for the AOS dashboard API (localhost:4096)
// ---------------------------------------------------------------------------

const API_BASE = "http://localhost:4096/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Task {
  id: string;
  title: string;
  status: "todo" | "active" | "done" | "blocked";
  priority: 1 | 2 | 3 | 4 | 5;
  project?: string;
  tags: string[];
  created: string;
  updated: string;
  completed?: string;
  subtasks: Subtask[];
}

export interface Subtask {
  id: string;
  title: string;
  done: boolean;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  taskCount: number;
  activeCount: number;
  completedCount: number;
}

export interface Briefing {
  date: string;
  greeting: string;
  activeTasks: Task[];
  upcomingEvents: BriefingEvent[];
  systemHealth: SystemHealth;
  summary: string;
}

export interface BriefingEvent {
  title: string;
  time: string;
  calendar: string;
}

export interface SystemHealth {
  services: { name: string; status: string }[];
  diskUsage: { internal: number; external: number };
  lastUpdate: string;
}

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------

async function apiFetch<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
    ...options,
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(
      `API ${options?.method || "GET"} ${path} failed (${res.status}): ${body}`
    );
  }

  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Tasks
// ---------------------------------------------------------------------------

export async function fetchTasks(): Promise<Task[]> {
  return apiFetch<Task[]>("/tasks");
}

export async function createTask(
  title: string,
  project?: string
): Promise<Task> {
  return apiFetch<Task>("/tasks", {
    method: "POST",
    body: JSON.stringify({ title, project }),
  });
}

export async function updateTask(
  id: string,
  updates: Partial<Pick<Task, "title" | "status" | "priority" | "project" | "tags">>
): Promise<Task> {
  return apiFetch<Task>(`/tasks/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });
}

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------

export async function fetchProjects(): Promise<Project[]> {
  return apiFetch<Project[]>("/projects");
}

// ---------------------------------------------------------------------------
// Briefing
// ---------------------------------------------------------------------------

export async function fetchBriefing(): Promise<Briefing> {
  return apiFetch<Briefing>("/briefing");
}
