/**
 * useProjectBrief — the compiled project brief.
 *
 * Mirrors `core/engine/work/brief_types.py` (BRIEF-CONTRACT.md, locked). The
 * brief is COMPILED from every signal that touched the project — tasks, git,
 * sessions, handoffs, vault docs — so nothing here is hand-maintained.
 *
 * Failure is not an error state. The brief endpoint may not be deployed yet on
 * a given machine, so a 404/503/timeout resolves to `{brief: null, unavailable:
 * true}` and the caller falls back to the plain task list. A project page must
 * never break because the compiler is behind.
 */

import { useQueries, useQuery } from '@tanstack/react-query';

const API = '/api';

// ── Contract types (brief_types.py) ─────────────────────────────────────────

export type BriefState = 'moving' | 'warm' | 'cold' | 'not_started' | 'blocked' | 'done';

export interface Actor {
  /** operator | agent | cron | import | unknown — never invented. */
  kind: string;
  name: string;
  session_id?: string | null;
  at?: string;
}

export interface Phase {
  key: string;
  label: string;
  task_ids: string[];
  done: number;
  total: number;
  /** not_started | in_progress | done | blocked */
  state: string;
}

export interface NextItem {
  task_id: string;
  title: string;
  why: string;
  priority: number;
}

export interface Blocker {
  task_id: string;
  title: string;
  blocked_on: string;
  since?: string | null;
}

export interface Conflict {
  /** duplicate_spine | orphan_task | stale_doc | untracked_repo | status_disagreement | no_body */
  kind: string;
  severity: 'warn' | 'error' | string;
  message: string;
  refs: string[];
}

export interface Artifact {
  /** initiative | spec | decision | council | session | commit | file | deck */
  kind: string;
  title: string;
  path: string;
  date?: string | null;
  excerpt?: string | null;
}

export interface BriefEvent {
  at: string;
  /** task_done | task_started | task_created | commit | session | handoff | decision */
  kind: string;
  text: string;
  ref?: string | null;
  actor: Actor;
}

export interface ProjectBrief {
  id: string;
  title: string;
  goal?: string | null;
  goal_title?: string | null;
  done_when?: string | null;
  appetite?: string | null;
  repo_path?: string | null;

  state: BriefState | string;
  state_reason: string;
  last_activity?: string | null;
  last_activity_source: string;

  task_count: number;
  done_count: number;
  active_count: number;
  todo_count: number;
  waiting_count: number;
  pct: number;

  summary: string;
  narrative?: string | null;
  narrative_written_at?: string | null;
  narrative_aged: boolean;

  tags: string[];
  phases: Phase[];
  next_up: NextItem[];
  blockers: Blocker[];
  conflicts: Conflict[];
  artifacts: Artifact[];
  recent_activity: BriefEvent[];

  sources: string[];
  compiled_at: string;
  compile_ms: number;
}

export interface BriefResult {
  brief: ProjectBrief | null;
  /** True when the compiler/endpoint could not answer — caller degrades, never errors. */
  unavailable: boolean;
}

const UNAVAILABLE: BriefResult = { brief: null, unavailable: true };

const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

/** Fill the array/scalar defaults the dataclass guarantees but a partial payload may omit. */
function normalize(b: Record<string, unknown>): ProjectBrief {
  return {
    ...(b as unknown as ProjectBrief),
    state: (b.state as string) || 'cold',
    state_reason: (b.state_reason as string) ?? '',
    last_activity_source: (b.last_activity_source as string) || 'unknown',
    task_count: Number(b.task_count ?? 0),
    done_count: Number(b.done_count ?? 0),
    active_count: Number(b.active_count ?? 0),
    todo_count: Number(b.todo_count ?? 0),
    waiting_count: Number(b.waiting_count ?? 0),
    pct: Number(b.pct ?? 0),
    summary: (b.summary as string) ?? '',
    narrative_aged: Boolean(b.narrative_aged),
    tags: arr<string>(b.tags),
    phases: arr<Phase>(b.phases),
    next_up: arr<NextItem>(b.next_up),
    blockers: arr<Blocker>(b.blockers),
    conflicts: arr<Conflict>(b.conflicts),
    artifacts: arr<Artifact>(b.artifacts),
    recent_activity: arr<BriefEvent>(b.recent_activity),
    sources: arr<string>(b.sources),
    compiled_at: (b.compiled_at as string) ?? '',
    compile_ms: Number(b.compile_ms ?? 0),
  };
}

async function fetchBrief(projectId: string): Promise<BriefResult> {
  let res: Response;
  try {
    res = await fetch(`${API}/work/projects/${encodeURIComponent(projectId)}/brief`, {
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    return UNAVAILABLE;                      // endpoint absent, offline, or hung
  }
  if (!res.ok) return UNAVAILABLE;           // 404 (not deployed) / 503 / 500
  try {
    const raw = await res.json();
    const body = (raw?.brief ?? raw) as Record<string, unknown> | null;
    if (!body || typeof body !== 'object' || typeof body.id !== 'string') return UNAVAILABLE;
    return { brief: normalize(body), unavailable: false };
  } catch {
    return UNAVAILABLE;
  }
}

/**
 * The brief for one project. Cache key `['project-brief', id]` is what the SSE
 * `project.brief.updated` listener invalidates — that is the whole live-update
 * mechanism, so do not key this any other way.
 */
export function useProjectBrief(projectId: string | null | undefined) {
  const q = useQuery({
    queryKey: ['project-brief', projectId],
    enabled: !!projectId,
    queryFn: () => fetchBrief(projectId!),
    staleTime: 15_000,
    retry: false,          // a missing endpoint must not be retried in a loop
  });
  return {
    brief: q.data?.brief ?? null,
    unavailable: q.data?.unavailable ?? false,
    isLoading: q.isLoading,
    isFetching: q.isFetching,
    refetch: q.refetch,
  };
}

/**
 * Briefs for a list of projects, sharing the same per-project cache entries as
 * `useProjectBrief` — so the Projects list warms the detail page, and one SSE
 * event refreshes whichever surface is mounted.
 */
export function useProjectBriefs(projectIds: string[]) {
  return useQueries({
    queries: projectIds.map(id => ({
      queryKey: ['project-brief', id],
      queryFn: () => fetchBrief(id),
      staleTime: 15_000,
      retry: false,
    })),
    combine: results => {
      const byId: Record<string, ProjectBrief> = {};
      results.forEach((r, i) => {
        const b = r.data?.brief;
        if (b) byId[projectIds[i]] = b;
      });
      return { byId, isLoading: results.some(r => r.isLoading) };
    },
  });
}
