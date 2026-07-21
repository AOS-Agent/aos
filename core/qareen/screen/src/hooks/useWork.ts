import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

const API = '/api';

export interface TaskHandoff {
  state: string;
  next_step: string;
  files: string[];
  decisions: string[];
  blockers: string[];
  session_id?: string;
  timestamp?: string;
}

/** Bug-class / pipeline richness carried in tasks.fields (never flattened). */
export interface TaskFields {
  root_cause?: string;
  code_refs?: string[];
  fix_approach?: string;
  severity?: number;      // 1 = crash/highest … 4 = trivial
  app?: string;           // app registry id → badge
  build?: string;
  screen?: string;
  branch?: string;
  attempts?: unknown[];
  proof?: unknown[];
  [k: string]: unknown;
}

export interface Task {
  id: string;
  title: string;
  status: 'triage' | 'backlog' | 'todo' | 'active' | 'waiting' | 'in_review' | 'done' | 'cancelled';
  priority: number;
  project: string | null;
  description: string | null;
  assigned_to: string | null;
  created_by: string | null;
  created: string;
  started: string | null;
  completed: string | null;
  due: string | null;
  tags: string[];
  parent_id: string | null;
  subtasks?: Task[];
  handoff?: TaskHandoff | null;
  recurrence: string | null;
  source?: string;
  // Kanban Phase 1: typed states, delegation, bug class.
  pipeline?: string | null;    // 'bug' for the bug fix loop
  stage?: string | null;       // fine pipeline stage, e.g. 'fixing'
  delegate?: string | null;    // agent executing this task
  held_by?: string | null;     // 'operator' | 'agent:<name>' | 'none'
  fields?: TaskFields;
}

/** True when a task is held by an agent (the purple-edge convention). */
export function heldAgent(task: Pick<Task, 'held_by' | 'delegate'>): string | null {
  const h = task.held_by;
  if (h && h.startsWith('agent:')) return h.slice('agent:'.length);
  return task.delegate ?? null;
}

export interface InboxItem {
  id: string;
  text: string;
  captured: string;
  source: string;
  snoozed_until?: string | null;
}

/** Provenance receipt parsed from an ambient inbox line: `… [comms 2026-07-12 · src im-42]`. */
export interface InboxReceipt {
  channel: string;
  date: string;
  ref: string;
  /** The commitment text with the trailing receipt stripped. */
  body: string;
}

const RECEIPT_RE = /^(.*?)\s*\[(\w+)\s+([\d-]+)\s*·\s*src\s+([^\]]+)\]\s*$/;

export function parseInboxReceipt(item: InboxItem): InboxReceipt | null {
  if (item.source !== 'ambient-commitment') return null;
  const m = RECEIPT_RE.exec(item.text ?? '');
  if (!m) return null;
  return { body: m[1].trim(), channel: m[2], date: m[3], ref: m[4].trim() };
}

export interface Project {
  id: string;
  title: string;
  status: string;
  goal?: string;
  /** Linked git repo path (projects.path). Presence gates the Git cockpit view. */
  path?: string | null;
  task_count?: number;
  done_count?: number;
  active_count?: number;
}

export interface KeyResult {
  title: string;
  progress: number;
  target: number;
}

export interface Goal {
  id: string;
  title: string;
  status?: string;
  description?: string;
  weight?: number;
  project?: string;
  key_results?: KeyResult[];
}

export interface WorkSummary {
  total_tasks: number;
  by_status: Record<string, number>;
  by_priority?: Record<string, number>;
  projects?: number;
  goals?: number;
  threads?: number;
  inbox?: number;
}

export interface WorkData {
  tasks: Task[];
  projects: Project[];
  goals: unknown[];
  threads: unknown[];
  inbox: InboxItem[];
  /** Authoritative whole-table counts — the honest source for header numbers. */
  summary: WorkSummary;
  /** Task a session is actively holding right now, if any. */
  liveTaskId: string | null;
}

async function fetchWork(): Promise<WorkData> {
  const res = await fetch(`${API}/work`);
  if (!res.ok) throw new Error(`Work API error: ${res.status}`);
  const raw = await res.json();
  // The API returns inbox items as {content, created, source, snoozed_until};
  // normalize to the InboxItem shape the views consume (text/captured).
  const rawInbox: any[] = Array.isArray(raw.inbox) ? raw.inbox : (raw.inbox ?? []);
  const inbox: InboxItem[] = rawInbox.map((i) => ({
    id: i.id,
    text: i.text ?? i.content ?? '',
    captured: i.captured ?? i.created ?? '',
    source: i.source ?? 'manual',
    snoozed_until: i.snoozed_until ?? null,
  }));
  return {
    tasks: Array.isArray(raw.tasks) ? raw.tasks : (raw.tasks?.tasks ?? []),
    projects: Array.isArray(raw.projects) ? raw.projects : (raw.projects?.projects ?? []),
    goals: Array.isArray(raw.goals) ? raw.goals : (raw.goals?.goals ?? []),
    threads: Array.isArray(raw.threads) ? raw.threads : (raw.threads?.threads ?? []),
    inbox,
    summary: (raw.summary && Object.keys(raw.summary).length
      ? raw.summary
      : { total_tasks: 0, by_status: {} }) as WorkSummary,
    liveTaskId: raw.live_task_id ?? null,
  } as WorkData;
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

/**
 * Authoritative status counts for header chips. Derived from the whole-table
 * summary the API now returns — NOT from the (bounded) returned task list, so
 * "N active / N todo / N done" are always true.
 */
export function useWorkCounts() {
  const { data, ...rest } = useWork();
  const by = data?.summary?.by_status ?? {};
  return {
    counts: {
      active: (by.active ?? 0) + (by.waiting ?? 0),
      todo: by.todo ?? 0,
      done: by.done ?? 0,
      cancelled: by.cancelled ?? 0,
      total: data?.summary?.total_tasks ?? 0,
    },
    liveTaskId: data?.liveTaskId ?? null,
    ...rest,
  };
}

// ── Delegation — the state transition + task.delegated event ────────────────

/**
 * Delegate a task to an agent, or take it back (hold). Delegation is a state
 * transition (spec §3.1): the agent becomes the holder and the task moves into
 * a started stage; assigned_to (the accountable human) is untouched. No runner
 * yet (Phase 4-5) — this records the holder + fires task.delegated.
 */
export function useDelegate() {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['work'] });
    qc.invalidateQueries({ queryKey: ['project-tasks'] });
  };

  const delegate = useMutation({
    mutationFn: async ({ id, agent }: { id: string; agent: string }) => {
      const res = await fetch(`${API}/tasks/${id}/delegate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent }),
      });
      if (!res.ok) throw new Error(`Delegate failed: ${res.status}`);
      return res.json();
    },
    onSuccess: invalidate,
  });

  const hold = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${API}/tasks/${id}/hold`, { method: 'POST' });
      if (!res.ok) throw new Error(`Hold failed: ${res.status}`);
      return res.json();
    },
    onSuccess: invalidate,
  });

  return { delegate, hold };
}

// ── Board statuses — category-colored columns + bug-stage labels ────────────

export interface StatusDef {
  id: string;
  name: string;
  category: string;
  color: string;
  position: number;
  is_default: boolean;
  pipeline: string | null;
}

async function fetchStatuses(): Promise<StatusDef[]> {
  const res = await fetch(`${API}/statuses`);
  if (!res.ok) throw new Error(`Statuses API error: ${res.status}`);
  const data = await res.json();
  return Array.isArray(data.statuses) ? data.statuses : [];
}

/** All status definitions. Generic columns (pipeline == null) drive the board;
 *  bug-pipeline rows (pipeline == 'bug') label bug cards. */
export function useStatuses() {
  const { data, ...rest } = useQuery({
    queryKey: ['statuses'],
    queryFn: fetchStatuses,
    staleTime: 300_000,
  });
  const all = data ?? [];
  return {
    statuses: all,
    generic: all.filter(s => !s.pipeline),
    bugStages: all.filter(s => s.pipeline === 'bug'),
    ...rest,
  };
}

// ── Inbox triage — the single write path used by every view ────────────────

export function useInboxTriage() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ['work'] });

  const promote = useMutation({
    mutationFn: async ({ id, title, project, priority }: {
      id: string; title?: string; project?: string | null; priority?: number;
    }) => {
      const res = await fetch(`${API}/inbox/${id}/promote`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, project, priority }),
      });
      if (!res.ok) throw new Error(`Promote failed: ${res.status}`);
      return res.json();
    },
    onSuccess: invalidate,
  });

  const dismiss = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${API}/inbox/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`Dismiss failed: ${res.status}`);
    },
    onSuccess: invalidate,
  });

  const snooze = useMutation({
    mutationFn: async ({ id, until }: { id: string; until?: string }) => {
      const res = await fetch(`${API}/inbox/${id}/snooze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ until }),
      });
      if (!res.ok) throw new Error(`Snooze failed: ${res.status}`);
      return res.json();
    },
    onSuccess: invalidate,
  });

  return { promote, dismiss, snooze };
}
