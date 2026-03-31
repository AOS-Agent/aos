import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type {
  PersonListResponse,
  PersonDetailResponse,
  PersonSurfaceResponse,
  UpdatePersonRequest,
} from '@/lib/types';

const API = '/api';

async function fetchPeople(query?: string): Promise<PersonListResponse> {
  const url = query ? `${API}/people?q=${encodeURIComponent(query)}` : `${API}/people`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`People API error: ${res.status}`);
  return res.json();
}

async function fetchPerson(id: string): Promise<PersonDetailResponse> {
  const res = await fetch(`${API}/people/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`Person API error: ${res.status}`);
  return res.json();
}

async function fetchSurfaces(): Promise<PersonSurfaceResponse> {
  const res = await fetch(`${API}/people/surfaces`);
  if (!res.ok) throw new Error(`Surfaces API error: ${res.status}`);
  return res.json();
}

export function usePeople(query?: string) {
  return useQuery({
    queryKey: ['people', query ?? ''],
    queryFn: () => fetchPeople(query),
    staleTime: 30_000,
    refetchInterval: 120_000,
  });
}

export function usePerson(id: string | null) {
  return useQuery({
    queryKey: ['person', id],
    queryFn: () => fetchPerson(id!),
    staleTime: 30_000,
    enabled: !!id,
  });
}

export function usePersonSurfaces() {
  return useQuery({
    queryKey: ['people-surfaces'],
    queryFn: fetchSurfaces,
    staleTime: 30_000,
    refetchInterval: 300_000,
  });
}

export function useUpdatePerson() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, data }: { id: string; data: UpdatePersonRequest }) => {
      const res = await fetch(`${API}/people/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error(`Update person failed: ${res.status}`);
      return res.json();
    },
    onSuccess: (_, { id }) => {
      qc.invalidateQueries({ queryKey: ['people'] });
      qc.invalidateQueries({ queryKey: ['person', id] });
    },
  });
}
