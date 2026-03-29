'use client';

import { useQuery } from '@tanstack/react-query';

const API = 'http://localhost:4096/api';

export interface ServiceStatus {
  status: 'online' | 'offline' | 'unknown';
  detail: string;
}

export type ServicesMap = Record<string, ServiceStatus>;

async function fetchServices(): Promise<ServicesMap> {
  const res = await fetch(`${API}/services`);
  if (!res.ok) throw new Error(`Services API error: ${res.status}`);
  return res.json();
}

export function useServices() {
  return useQuery({
    queryKey: ['services'],
    queryFn: fetchServices,
    refetchInterval: 60_000,  // SSE handles real-time; this is a fallback
  });
}
