// ---------------------------------------------------------------------------
// Converse data layer — react-query hooks over core/qareen/api/converse.py.
//
// Mirrors the useShipments.ts pattern: typed queries + mutations, no
// hand-rolled fetch in components. Live updates ride on useConverseStream.ts
// invalidating these same query keys — components never need to poll.
// ---------------------------------------------------------------------------

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  converseApi,
  type SessionStatusFilter,
} from '@/lib/converseApi';

export function sessionsKey(filters: { status?: SessionStatusFilter; channel?: string; mode?: string } = {}) {
  return ['converse-sessions', filters] as const;
}
export function sessionKey(id: string | undefined) {
  return ['converse-session', id] as const;
}
export function actionsKey() {
  return ['converse-actions'] as const;
}

export function useConverseSessions(
  filters: { status?: SessionStatusFilter; channel?: string; mode?: string; limit?: number } = { status: 'active' },
) {
  return useQuery({
    queryKey: sessionsKey({ status: filters.status, channel: filters.channel, mode: filters.mode }),
    queryFn: () => converseApi.sessions(filters),
    staleTime: 10_000,
  });
}

export function useConverseSession(id: string | undefined) {
  return useQuery({
    queryKey: sessionKey(id),
    enabled: !!id,
    queryFn: () => converseApi.session(id!),
    staleTime: 5_000,
  });
}

export function useConverseActions() {
  return useQuery({
    queryKey: actionsKey(),
    queryFn: () => converseApi.pendingActions(),
    staleTime: 10_000,
  });
}

function useInvalidateConverse() {
  const qc = useQueryClient();
  return (id?: string) => {
    qc.invalidateQueries({ queryKey: ['converse-sessions'] });
    if (id) qc.invalidateQueries({ queryKey: sessionKey(id) });
    else qc.invalidateQueries({ queryKey: ['converse-session'] });
  };
}

export function usePauseSession() {
  const invalidate = useInvalidateConverse();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) => converseApi.pause(id, reason),
    onSuccess: (_data, vars) => invalidate(vars.id),
  });
}

export function useResumeSession() {
  const invalidate = useInvalidateConverse();
  return useMutation({
    mutationFn: (id: string) => converseApi.resume(id),
    onSuccess: (_data, id) => invalidate(id),
  });
}

export function useTakeoverSession() {
  const invalidate = useInvalidateConverse();
  return useMutation({
    mutationFn: (id: string) => converseApi.takeover(id),
    onSuccess: (_data, id) => invalidate(id),
  });
}

export function useReleaseSession() {
  const invalidate = useInvalidateConverse();
  return useMutation({
    mutationFn: (id: string) => converseApi.release(id),
    onSuccess: (_data, id) => invalidate(id),
  });
}

export function useInjectMessage() {
  const invalidate = useInvalidateConverse();
  return useMutation({
    mutationFn: ({ id, text, deliver }: { id: string; text: string; deliver: 'note' | 'send' }) =>
      converseApi.inject(id, text, deliver),
    onSuccess: (_data, vars) => invalidate(vars.id),
  });
}

export function useCloseSession() {
  const invalidate = useInvalidateConverse();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) => converseApi.close(id, reason),
    onSuccess: (_data, vars) => invalidate(vars.id),
  });
}

export function useApproveAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => converseApi.approveAction(id),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: actionsKey() });
      qc.invalidateQueries({ queryKey: sessionKey(data.action.session_id) });
    },
  });
}

export function useRejectAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => converseApi.rejectAction(id),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: actionsKey() });
      qc.invalidateQueries({ queryKey: sessionKey(data.action.session_id) });
    },
  });
}
