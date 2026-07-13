import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

const API = '/api';

export interface Agent {
  id: string;
  name: string;
  role: string;
  domain: string;
  description: string;
  model: string;
  color: string;
  initials: string;
  tools: string[];
  skills: string[];
  mcp_servers: string[];
  scope: string;
  reports_to: string | null;
  source: string;          // system | catalog | community
  default_trust: number;
  is_system: boolean;
  is_active: boolean;
  schedule: Record<string, string>;
}

interface AgentListResponse {
  agents: Agent[];
  total: number;
  active_count: number;
  system_count: number;
}

interface CatalogResponse {
  catalog: Agent[];
  total: number;
}

async function fetchAgents(): Promise<Agent[]> {
  const res = await fetch(`${API}/agents`);
  if (!res.ok) throw new Error(`Agents API error: ${res.status}`);
  const data: AgentListResponse = await res.json();
  const agents = data.agents ?? [];
  // Deduplicate by name — prefer system agents
  const seen = new Map<string, Agent>();
  for (const agent of agents) {
    const key = agent.name.toLowerCase();
    if (!seen.has(key)) {
      seen.set(key, agent);
    } else if (agent.is_system && !seen.get(key)!.is_system) {
      seen.set(key, agent);
    }
  }
  return Array.from(seen.values());
}

async function fetchCatalog(): Promise<Agent[]> {
  const res = await fetch(`${API}/agents/catalog`);
  if (!res.ok) throw new Error(`Catalog API error: ${res.status}`);
  const data: CatalogResponse = await res.json();
  return data.catalog ?? [];
}

async function fetchAgent(id: string): Promise<Agent> {
  const res = await fetch(`${API}/agents/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`Agent API error: ${res.status}`);
  return res.json();
}

/** Installed agents on this machine */
export function useAgents() {
  return useQuery({
    queryKey: ['agents'],
    queryFn: fetchAgents,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

/** Catalog agents available to hire */
export function useCatalog() {
  return useQuery({
    queryKey: ['agents', 'catalog'],
    queryFn: fetchCatalog,
    staleTime: 60_000,
  });
}

/** Single agent detail */
export function useAgent(id: string | null) {
  return useQuery({
    queryKey: ['agents', id],
    queryFn: () => fetchAgent(id!),
    enabled: !!id,
    staleTime: 30_000,
  });
}

/** Activate a catalog agent */
export function useActivateAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (agentId: string) => {
      const res = await fetch(`${API}/agents/${agentId}/activate`, { method: 'POST' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error ?? `Activate failed: ${res.status}`);
      }
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agents'] });
    },
  });
}

/** Install a community agent from GitHub */
export function useInstallCommunity() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: { repo: string; file: string; id: string }) => {
      const res = await fetch(`${API}/agents/community/install`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(args),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error ?? `Install failed: ${res.status}`);
      }
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agents'] });
    },
  });
}
