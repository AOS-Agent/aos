// ---------------------------------------------------------------------------
// Git/Ship cockpit API — typed wrappers over /api/git/{project_id}/*.
//
// Mirrors lib/sentinelApi.ts. All reads are bounded server-side; the client
// never auto-polls (slow external-SSD git must not thrash). Manual refresh only.
// ---------------------------------------------------------------------------

import { api } from './api';

// -- Status -----------------------------------------------------------------

export interface DirtyCounts {
  staged: number;
  unstaged: number;
  untracked: number;
  total: number;
}

/** Response of GET /status. `linked`/`is_repo` describe graceful non-repo states. */
export interface GitStatus {
  linked: boolean;
  is_repo?: boolean;
  reason?: string;
  error?: string;
  branch?: string;
  detached?: boolean;
  head?: string;
  head_sha?: string;
  base?: string | null;
  ahead?: number;
  behind?: number;
  base_missing?: boolean;
  dirty?: DirtyCounts;
  worktree_count?: number;
}

// -- Commits ----------------------------------------------------------------

export interface GitCommit {
  sha: string;
  short: string;
  parents: string[];
  author: string;
  ts: number;
  refs: string[];
  subject: string;
}

export interface CommitsResponse {
  commits: GitCommit[];
  total: number;
  truncated: boolean;
  base?: string | null;
  base_missing?: boolean;
  error?: string;
}

/** Bounded merged context beneath origin/main (dimmed below the ship line). */
export interface GraphBelowResponse {
  commits: GitCommit[];
  base?: string | null;
  base_missing?: boolean;
  error?: string;
}

// -- Worktrees --------------------------------------------------------------

export interface Worktree {
  path: string;
  head?: string;
  head_sha?: string;
  branch?: string;
  detached?: boolean;
  bare?: boolean;
  locked?: boolean;
  primary?: boolean;
  is_current?: boolean;
}

export interface WorktreesResponse {
  worktrees: Worktree[];
  count: number;
  linked?: boolean;
  is_repo?: boolean;
  error?: string;
}

// -- Batches / ship plan ----------------------------------------------------

export type BatchStatus = 'built' | 'half-baked' | 'broken' | 'unknown';
export type BatchDecision = 'undecided' | 'ship' | 'defer' | 'hold';
export type GateStatus = 'unknown' | 'running' | 'pass' | 'warn' | 'fail';

export interface Batch {
  id: string;
  ordinal: number;
  title: string;
  commit_count: number;
  commit_count_live?: number;
  commits: string[];
  status: BatchStatus;
  decision: BatchDecision;
  suggested_decision: BatchDecision;
  suggested: boolean;
  rationale: string;
  watch_items: string[];
  assignment: string;
  decided_by?: string | null;
  decided_at?: number | null;
  note?: string;
}

export interface Gate {
  id: string;
  scope: string;
  status: GateStatus;
  summary: string;
  exit_code?: number | null;
  ran_at?: number | null;
  ran_against?: string | null;
  source?: string;
}

export interface BatchesResponse {
  // graceful non-repo shapes reuse these optional fields:
  linked?: boolean;
  is_repo?: boolean;
  error?: string;
  batches: Batch[];
  source: string;
  status?: string;
  drift?: boolean;
  overflow?: boolean;
  /** sha → subject for the whole unmerged set — lets the ledger show every
   *  batch commit's message regardless of the graph's loaded window. */
  subjects?: Record<string, string>;
  gates?: Record<string, Gate>;
  seed?: {
    source_refs?: string[];
    seeded_at?: number;
    seeded_head?: string;
    ahead?: number;
    behind?: number;
  };
  total_unmerged: number;
  total?: number;
  base?: string | null;
}

export interface DecisionPayload {
  decision: BatchDecision;
  status?: BatchStatus;
  note?: string;
}

export interface DecisionResponse {
  ok: boolean;
  batch?: Batch;
  plan_status?: string;
  error?: string;
}

const enc = encodeURIComponent;

export const gitApi = {
  status: (projectId: string) =>
    api.get<GitStatus>(`/git/${enc(projectId)}/status`),

  commits: (projectId: string, limit = 60, base = 'origin/main') =>
    api.get<CommitsResponse>(
      `/git/${enc(projectId)}/commits?base=${enc(base)}&limit=${limit}`,
    ),

  graphBelow: (projectId: string, limit = 6, base = 'origin/main') =>
    api.get<GraphBelowResponse>(
      `/git/${enc(projectId)}/graph?base=${enc(base)}&limit=${limit}`,
    ),

  worktrees: (projectId: string) =>
    api.get<WorktreesResponse>(`/git/${enc(projectId)}/worktrees`),

  batches: (projectId: string, base = 'origin/main') =>
    api.get<BatchesResponse>(`/git/${enc(projectId)}/batches?base=${enc(base)}`),

  setDecision: (projectId: string, batchId: string, body: DecisionPayload) =>
    api.post<DecisionResponse>(
      `/git/${enc(projectId)}/batches/${enc(batchId)}/decision`,
      body,
    ),
};

// -- Display helpers --------------------------------------------------------

/** A batch's stable color "tone" key (areaStyle tag palette) by ordinal. */
const BATCH_TONE_KEYS = ['green', 'purple', 'blue', 'orange', 'teal', 'pink'] as const;
export function batchToneKey(ordinal: number): (typeof BATCH_TONE_KEYS)[number] {
  return BATCH_TONE_KEYS[(Math.max(1, ordinal) - 1) % BATCH_TONE_KEYS.length];
}

export function decisionLabel(d: BatchDecision): string {
  return d === 'undecided' ? 'undecided' : d;
}

export function statusLabel(s: BatchStatus): string {
  return s === 'half-baked' ? 'half-baked' : s;
}
