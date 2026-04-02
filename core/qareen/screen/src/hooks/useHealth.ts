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
  const data = await res.json();
  return {
    disk_pct: data.disk_pct ?? 0,
    disk_free_gb: data.disk_free_gb ?? 0,
    ram_pct: data.ram_pct ?? 0,
    ram_used_gb: data.ram_used_gb ?? 0,
  };
}

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: fetchHealth,
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
}
