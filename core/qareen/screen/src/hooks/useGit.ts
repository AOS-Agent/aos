// ---------------------------------------------------------------------------
// Git cockpit data hooks — react-query over gitApi.
//
// staleTime 30s, NO refetchInterval: slow external-SSD git must not thrash.
// The cockpit refreshes only on mount and on an explicit operator Refresh.
// ---------------------------------------------------------------------------

import { useEffect } from 'react';
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  gitApi,
  type BatchesResponse,
  type CommitsResponse,
  type DecisionPayload,
  type GitStatus,
  type GraphBelowResponse,
  type ShipPlanResponse,
  type WorktreesResponse,
} from '@/lib/gitApi';

const STALE = 30_000;

export function useGitStatus(projectId: string, path?: string | null) {
  return useQuery<GitStatus>({
    queryKey: ['git', 'status', projectId],
    queryFn: () => gitApi.status(projectId),
    enabled: !!path && !!projectId,
    staleTime: STALE,
    retry: 1,
  });
}

export function useGitCommits(
  projectId: string,
  path?: string | null,
  limit = 60,
) {
  return useQuery<CommitsResponse>({
    queryKey: ['git', 'commits', projectId, limit],
    queryFn: () => gitApi.commits(projectId, limit),
    enabled: !!path && !!projectId,
    staleTime: STALE,
    // Keep the prior page rendered while a larger limit loads — "Load older"
    // grows the graph in place instead of flashing back to the skeleton.
    placeholderData: keepPreviousData,
    retry: 1,
  });
}

/** Bounded merged context beneath the ship line — dimmed in the graph. */
export function useGitBelow(projectId: string, path?: string | null, limit = 6) {
  return useQuery<GraphBelowResponse>({
    queryKey: ['git', 'below', projectId, limit],
    queryFn: () => gitApi.graphBelow(projectId, limit),
    enabled: !!path && !!projectId,
    staleTime: STALE,
    retry: 1,
  });
}

/** Worktrees attached to the repo (this checkout + any siblings). */
export function useGitWorktrees(projectId: string, path?: string | null) {
  return useQuery<WorktreesResponse>({
    queryKey: ['git', 'worktrees', projectId],
    queryFn: () => gitApi.worktrees(projectId),
    enabled: !!path && !!projectId,
    staleTime: STALE,
    retry: 1,
  });
}

export function useShipPlan(projectId: string, path?: string | null) {
  return useQuery<BatchesResponse>({
    queryKey: ['git', 'batches', projectId],
    queryFn: () => gitApi.batches(projectId),
    enabled: !!path && !!projectId,
    staleTime: STALE,
    retry: 1,
  });
}

export function useSetBatchDecision(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { batchId: string; body: DecisionPayload }) =>
      gitApi.setDecision(projectId, vars.batchId, vars.body),
    // Optimistic re-tint: patch the cached plan so the row/graph update instantly.
    onMutate: async (vars) => {
      await qc.cancelQueries({ queryKey: ['git', 'batches', projectId] });
      const prev = qc.getQueryData<BatchesResponse>(['git', 'batches', projectId]);
      if (prev) {
        qc.setQueryData<BatchesResponse>(['git', 'batches', projectId], {
          ...prev,
          batches: prev.batches.map((b) =>
            b.id === vars.batchId
              ? {
                  ...b,
                  decision: vars.body.decision,
                  status: vars.body.status ?? b.status,
                }
              : b,
          ),
        });
      }
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(['git', 'batches', projectId], ctx.prev);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['git', 'batches', projectId] });
      qc.invalidateQueries({ queryKey: ['git', 'ship-plan', projectId] });
    },
  });
}

/** Ship readiness + the generated command plan. Recomputes on gate/decision changes. */
export function useShipReadiness(projectId: string, path?: string | null) {
  return useQuery<ShipPlanResponse>({
    queryKey: ['git', 'ship-plan', projectId],
    queryFn: () => gitApi.shipPlan(projectId),
    enabled: !!path && !!projectId,
    staleTime: STALE,
    retry: 1,
  });
}

/** Kick off the ship gates (background run; progress arrives over the SSE stream). */
export function useRunGates(projectId: string) {
  return useMutation({ mutationFn: () => gitApi.runGates(projectId) });
}

/**
 * Live gate progress — subscribes to /api/stream while the cockpit is open and
 * patches the cached plan's gate chips as `gate.progress` events arrive, so they
 * flip running → pass/warn/fail without a refetch. On the terminal `*` event we
 * invalidate batches to settle on the persisted (stale-annotated) state.
 */
export function useGateProgress(projectId: string, enabled: boolean) {
  const qc = useQueryClient();
  useEffect(() => {
    if (!enabled || !projectId) return;
    const es = new EventSource('/api/stream');
    es.addEventListener('gate.progress', (e) => {
      try {
        const evt = JSON.parse((e as MessageEvent).data);
        const p = evt.payload ?? {};
        if (p.project_id !== projectId) return;
        if (p.gate === '*') {
          if (p.status === 'done') {
            qc.invalidateQueries({ queryKey: ['git', 'batches', projectId] });
            qc.invalidateQueries({ queryKey: ['git', 'ship-plan', projectId] });
          }
          return;
        }
        qc.setQueryData<BatchesResponse>(['git', 'batches', projectId], (prev) => {
          if (!prev) return prev;
          const gates = { ...(prev.gates ?? {}) };
          const g = gates[p.gate] ?? {
            id: p.gate,
            scope: 'plan',
            status: 'unknown',
            summary: '',
          };
          gates[p.gate] = {
            ...g,
            status: p.status,
            summary:
              p.summary ?? (p.status === 'running' ? 'running…' : g.summary),
            exit_code: p.exit_code ?? g.exit_code,
            ran_at: p.ran_at ?? g.ran_at,
            ran_against: p.ran_against ?? g.ran_against,
            stale: false,
          };
          return { ...prev, gates };
        });
      } catch {
        /* ignore malformed frames */
      }
    });
    return () => es.close();
  }, [projectId, enabled, qc]);
}

/** Imperative refresh of all cockpit queries for this project (manual Refresh). */
export function useGitRefresh(projectId: string) {
  const qc = useQueryClient();
  return () =>
    qc.invalidateQueries({
      predicate: (q) => {
        const k = q.queryKey as unknown[];
        return k[0] === 'git' && k.includes(projectId);
      },
    });
}
