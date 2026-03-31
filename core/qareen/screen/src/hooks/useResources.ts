import { useQuery, useQueryClient } from '@tanstack/react-query';

const API = '/api';

export interface DriveInfo {
  label: string;
  mount: string;
  total_gb: number;
  used_gb: number;
  free_gb: number;
  pct: number;
}

export interface DirCategory {
  name: string;
  path: string;
  drive: string;
  size_gb: number;
  is_symlink: boolean;
  real_path: string | null;
}

export interface Cleanable {
  label: string;
  category: string;
  path: string;
  size_gb: number;
  safe: boolean;
  description: string;
}

export interface ProcessGroup {
  category: string;
  rss_mb: number;
  rss_gb: number;
  count: number;
  top_processes: { command: string; rss_mb: number }[];
}

export interface Recommendation {
  severity: 'high' | 'medium' | 'low';
  text: string;
  type: 'disk' | 'cleanup' | 'memory';
  path?: string;
}

export interface ResourcesData {
  drives: DriveInfo[];
  categories: DirCategory[];
  cleanables: Cleanable[];
  processes: ProcessGroup[];
  recommendations: Recommendation[];
  scanned_at: string;
}

async function fetchResources(refresh = false): Promise<ResourcesData> {
  const url = refresh ? `${API}/system/resources?refresh=true` : `${API}/system/resources`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Resources API error: ${res.status}`);
  return res.json();
}

export function useResources(enabled = false) {
  return useQuery({
    queryKey: ['resources'],
    queryFn: () => fetchResources(),
    enabled,
    staleTime: 5 * 60_000,
  });
}

export function useRefreshResources() {
  const qc = useQueryClient();
  return async () => {
    const data = await fetchResources(true);
    qc.setQueryData(['resources'], data);
    return data;
  };
}
