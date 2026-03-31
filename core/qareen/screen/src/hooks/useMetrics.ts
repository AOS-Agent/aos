import { useQuery } from '@tanstack/react-query';
import type { MetricListResponse } from '@/lib/types';

const API = '/api';

export function useMetrics() {
  return useQuery({
    queryKey: ['metrics'],
    queryFn: async (): Promise<MetricListResponse> => {
      const res = await fetch(`${API}/metrics`);
      if (!res.ok) throw new Error(`Metrics API error: ${res.status}`);
      return res.json();
    },
    refetchInterval: 300_000,
  });
}
