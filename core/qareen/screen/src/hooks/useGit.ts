// ---------------------------------------------------------------------------
// Git cockpit data hooks — react-query over gitApi.
//
// staleTime 30s, NO refetchInterval: slow external-SSD git must not thrash.
// The cockpit refreshes only on mount and on an explicit operator Refresh.
// ---------------------------------------------------------------------------

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
    },
  });
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
