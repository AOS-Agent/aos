'use client';

import { useQuery } from '@tanstack/react-query';

export interface AgentMeta {
  id: string;
  name: string;
  description: string;
  model: string;
  tools: string;
}

async function fetchAgents(): Promise<AgentMeta[]> {
  const res = await fetch('/api/agents');
  if (!res.ok) throw new Error(`Agents API error: ${res.status}`);
  return res.json();
}

export function useAgents() {
  return useQuery({
    queryKey: ['agents'],
    queryFn: fetchAgents,
    refetchInterval: 60_000,
  });
}
