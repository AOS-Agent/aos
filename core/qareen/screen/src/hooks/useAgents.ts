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
    const agents: AgentMeta[] = data.agents ?? data ?? [];
    // Deduplicate by name — keep the first occurrence (prefer system agents)
    const seen = new Map<string, AgentMeta>();
    for (const agent of agents) {
      const key = agent.name.toLowerCase();
      if (!seen.has(key)) {
        seen.set(key, agent);
      } else {
        // Prefer the one with is_system flag
        const existing = seen.get(key)!;
        if (agent.is_system && !existing.is_system) {
          seen.set(key, agent);
        }
      }
    }
    return Array.from(seen.values());
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
