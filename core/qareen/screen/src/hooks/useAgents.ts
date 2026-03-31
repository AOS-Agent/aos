import { useQuery } from '@tanstack/react-query';

const API = '/api';

export interface AgentMeta {
  id: string;
  name: string;
  description: string;
  model: string;
  tools: string | string[];
  is_system?: boolean;
  is_active?: boolean;
}

async function fetchAgents(): Promise<AgentMeta[]> {
  try {
    const res = await fetch(`${API}/agents`);
    if (!res.ok) throw new Error(`Agents API error: ${res.status}`);
    const data = await res.json();
    // API returns { agents: [...], total, active_count }
    return data.agents ?? data ?? [];
  } catch {
    return [];
  }
}

export function useAgents() {
  return useQuery({
    queryKey: ['agents'],
    queryFn: fetchAgents,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}
