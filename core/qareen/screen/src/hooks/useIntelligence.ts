import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

const API = '/api';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface IntelligenceItem {
  id: string;
  title: string;
  summary: string | null;
  content: string | null;
  url: string;
  author: string | null;
  platform: string;
  source_name: string;
  published_at: string;
  created_at: string;
  relevance_score: number;
  relevance_tags: string[];
  status: 'unread' | 'read' | 'saved' | 'dismissed';
  vault_path: string | null;
}

export interface IntelligenceSource {
  id: string;
  name: string;
  platform: string;
  route: string | null;
  route_url: string | null;
  priority: string;
  keywords: string[];
  is_active: boolean;
  last_checked: string | null;
  items_total: number;
}

export interface FeedResponse {
  items: IntelligenceItem[];
  total: number;
}

export interface StatsResponse {
  total_items: number;
  unread_count: number;
  sources_count: number;
  active_sources: number;
  last_ingest: string | null;
  platforms: Record<string, number>;
}

export interface SourcesResponse {
  sources: IntelligenceSource[];
}

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------

async function fetchFeed(
  days?: number,
  limit?: number,
  platform?: string,
  status?: string,
  search?: string,
): Promise<FeedResponse> {
  const params = new URLSearchParams();
  if (days) params.set('days', String(days));
  if (limit) params.set('limit', String(limit));
  if (platform) params.set('platform', platform);
  if (status) params.set('status', status);
  if (search) params.set('search', search);
  const qs = params.toString();
  const res = await fetch(`${API}/intelligence/feed${qs ? `?${qs}` : ''}`);
  if (!res.ok) throw new Error(`Feed API error: ${res.status}`);
  return res.json();
}

async function fetchItem(id: string): Promise<IntelligenceItem> {
  const res = await fetch(`${API}/intelligence/items/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`Item API error: ${res.status}`);
  return res.json();
}

async function fetchSources(): Promise<SourcesResponse> {
  const res = await fetch(`${API}/intelligence/sources`);
  if (!res.ok) throw new Error(`Sources API error: ${res.status}`);
  return res.json();
}

async function fetchStats(): Promise<StatsResponse> {
  const res = await fetch(`${API}/intelligence/stats`);
  if (!res.ok) throw new Error(`Stats API error: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useIntelligenceFeed(
  days?: number,
  limit?: number,
  platform?: string,
  status?: string,
  search?: string,
) {
  return useQuery({
    queryKey: ['intelligence-feed', days, limit, platform, status, search],
    queryFn: () => fetchFeed(days, limit, platform, status, search),
    staleTime: 30_000,
    refetchInterval: 120_000,
  });
}

export function useIntelligenceItem(id: string | null) {
  return useQuery({
    queryKey: ['intelligence-item', id],
    queryFn: () => fetchItem(id!),
    staleTime: 30_000,
    enabled: !!id,
  });
}

export function useIntelligenceSources() {
  return useQuery({
    queryKey: ['intelligence-sources'],
    queryFn: fetchSources,
    staleTime: 60_000,
  });
}

export function useIntelligenceStats() {
  return useQuery({
    queryKey: ['intelligence-stats'],
    queryFn: fetchStats,
    staleTime: 30_000,
    refetchInterval: 120_000,
  });
}

// ---------------------------------------------------------------------------
// Mutation hooks
// ---------------------------------------------------------------------------

export function useSaveItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${API}/intelligence/items/${encodeURIComponent(id)}/save`, {
        method: 'POST',
      });
      if (!res.ok) throw new Error(`Save failed: ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['intelligence-feed'] });
      qc.invalidateQueries({ queryKey: ['intelligence-stats'] });
    },
  });
}

export function useDismissItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${API}/intelligence/items/${encodeURIComponent(id)}/dismiss`, {
        method: 'POST',
      });
      if (!res.ok) throw new Error(`Dismiss failed: ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['intelligence-feed'] });
      qc.invalidateQueries({ queryKey: ['intelligence-stats'] });
    },
  });
}

export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${API}/intelligence/items/${encodeURIComponent(id)}/read`, {
        method: 'POST',
      });
      if (!res.ok) throw new Error(`Mark read failed: ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['intelligence-feed'] });
      qc.invalidateQueries({ queryKey: ['intelligence-stats'] });
    },
  });
}

export function useCreateSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: {
      name: string;
      platform: string;
      route?: string;
      route_url?: string;
      priority?: string;
      keywords?: string[];
    }) => {
      const res = await fetch(`${API}/intelligence/sources`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error(`Create source failed: ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['intelligence-sources'] });
      qc.invalidateQueries({ queryKey: ['intelligence-stats'] });
    },
  });
}

export function useDeleteSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${API}/intelligence/sources/${encodeURIComponent(id)}`, {
        method: 'DELETE',
      });
      if (!res.ok) throw new Error(`Delete source failed: ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['intelligence-sources'] });
      qc.invalidateQueries({ queryKey: ['intelligence-stats'] });
    },
  });
}
