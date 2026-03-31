import { useQuery } from '@tanstack/react-query';

const API = '/api';

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

async function fetchCrons(): Promise<CronJob[]> {
  const res = await fetch(`${API}/crons`);
  if (!res.ok) throw new Error(`Crons API error: ${res.status}`);
  const data = await res.json();
  // API returns { crons: [...], total }
  return data.crons ?? data.jobs ?? data ?? [];
}

export function useCrons() {
  const { data, ...rest } = useQuery({
    queryKey: ['crons'],
    queryFn: fetchCrons,
    staleTime: 30_000,
    refetchInterval: 120_000,
  });
  return { crons: data ?? [], ...rest };
}
