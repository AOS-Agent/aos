import { useQuery } from '@tanstack/react-query';

const API = '/api';

export interface SystemHealth {
  disk_pct: number;
  disk_free_gb: number;
  ram_pct: number;
  ram_used_gb: number;
}

async function fetchHealth(): Promise<SystemHealth> {
  const res = await fetch(`${API}/health`);
  if (!res.ok) throw new Error(`Health API error: ${res.status}`);
  return res.json();
}

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: fetchHealth,
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
}
