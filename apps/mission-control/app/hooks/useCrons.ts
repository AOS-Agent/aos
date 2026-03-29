'use client';

import { useQuery } from '@tanstack/react-query';

const API = 'http://localhost:4096/api';

export interface CronJob {
  name: string;
  schedule: string;
  enabled: boolean;
  last_run: string | null;
  exit_code: number | null;
  duration_s: number | null;
  run_count: number;
  last_failure: string | null;
  status: 'ok' | 'failed' | 'unknown';
}

async function fetchCrons(): Promise<{ jobs: CronJob[] }> {
  const res = await fetch(`${API}/crons`);
  if (!res.ok) throw new Error(`Crons API error: ${res.status}`);
  return res.json();
}

export function useCrons() {
  const { data, ...rest } = useQuery({
    queryKey: ['crons'],
    queryFn: fetchCrons,
    refetchInterval: 120_000,
  });
  return { crons: data?.jobs ?? [], ...rest };
}
