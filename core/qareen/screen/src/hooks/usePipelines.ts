import { useQuery } from '@tanstack/react-query';
import type { PipelineListResponse, PipelineRunListResponse } from '@/lib/types';

const API = '/api';

export function usePipelines() {
  return useQuery({
    queryKey: ['pipelines'],
    queryFn: async (): Promise<PipelineListResponse> => {
      const res = await fetch(`${API}/pipelines`);
      if (!res.ok) throw new Error(`Pipelines API error: ${res.status}`);
      return res.json();
    },
    refetchInterval: 120_000,
  });
}

export function usePipelineRuns(name: string | null) {
  return useQuery({
    queryKey: ['pipeline-runs', name],
    queryFn: async (): Promise<PipelineRunListResponse> => {
      const res = await fetch(`${API}/pipelines/${name}/runs`);
      if (!res.ok) throw new Error(`Pipeline runs API error: ${res.status}`);
      return res.json();
    },
    enabled: !!name,
    refetchInterval: 30_000,
  });
}
