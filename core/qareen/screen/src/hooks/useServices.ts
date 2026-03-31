import { useQuery } from '@tanstack/react-query';

const API = '/api';

export interface ServiceInfo {
  name: string;
  status: string;
  port?: number | null;
  pid?: number | null;
  uptime_seconds?: number | null;
  last_check?: string;
  error?: string | null;
}

async function fetchServices(): Promise<ServiceInfo[]> {
  const res = await fetch(`${API}/services`);
  if (!res.ok) throw new Error(`Services API error: ${res.status}`);
  const data = await res.json();
  // API returns { services: [...], total, healthy_count }
  return data.services ?? data ?? [];
}

export function useServices() {
  return useQuery({
    queryKey: ['services'],
    queryFn: fetchServices,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}
