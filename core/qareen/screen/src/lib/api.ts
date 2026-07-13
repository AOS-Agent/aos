// ---------------------------------------------------------------------------
// HTTP client for the AOS Qareen API (proxied via Vite to :4096)
// ---------------------------------------------------------------------------

const API_BASE = "/api";

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  status: number;
  body: string;

  constructor(method: string, path: string, status: number, body: string) {
    super(`API ${method} ${path} failed (${status}): ${body}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

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
    throw new ApiError(
      options?.method || "GET",
      path,
      res.status,
      body,
    );
  }

  // Handle 204 No Content
  if (res.status === 204) return undefined as T;

  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Convenience methods
// ---------------------------------------------------------------------------

export const api = {
  get<T>(path: string): Promise<T> {
    return apiFetch<T>(path);
  },

  post<T>(path: string, body?: unknown): Promise<T> {
    return apiFetch<T>(path, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    });
  },

  patch<T>(path: string, body: unknown): Promise<T> {
    return apiFetch<T>(path, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },

  delete<T>(path: string): Promise<T> {
    return apiFetch<T>(path, {
      method: "DELETE",
    });
  },
};

// ---------------------------------------------------------------------------
// Legacy exports for backward compatibility
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

export async function fetchTasks(): Promise<Task[]> {
  return api.get<Task[]>("/tasks");
}

export async function createTask(
  title: string,
  project?: string
): Promise<Task> {
  return api.post<Task>("/tasks", { title, project });
}

export async function updateTask(
  id: string,
  updates: Partial<Pick<Task, "title" | "status" | "priority" | "project" | "tags">>
): Promise<Task> {
  return api.patch<Task>(`/tasks/${encodeURIComponent(id)}`, updates);
}

export async function fetchProjects(): Promise<Project[]> {
  return api.get<Project[]>("/projects");
}

export async function fetchBriefing(): Promise<Briefing> {
  return api.get<Briefing>("/briefing");
}
