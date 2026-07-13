import { useQuery } from '@tanstack/react-query';

const API = '/api';

export interface Skill {
  id: string;
  name: string;
  description: string;
  triggers: string[];
  category: string;   // core | domain | workflow | integration
  allowed_tools: string[];
  body?: string | null;
  is_active: boolean;
  source_path: string | null;
}

interface SkillListResponse {
  skills: Skill[];
  total: number;
  active_count: number;
}

async function fetchSkills(): Promise<Skill[]> {
  const res = await fetch(`${API}/skills`);
  if (!res.ok) throw new Error(`Skills API error: ${res.status}`);
  const data: SkillListResponse = await res.json();
  return data.skills ?? [];
}

export function useSkills() {
  return useQuery({
    queryKey: ['skills'],
    queryFn: fetchSkills,
    staleTime: 60_000,
  });
}

async function fetchSkill(id: string): Promise<Skill> {
  const res = await fetch(`${API}/skills/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`Skill API error: ${res.status}`);
  return res.json();
}

export function useSkill(id: string | null) {
  return useQuery({
    queryKey: ['skills', id],
    queryFn: () => fetchSkill(id!),
    enabled: !!id,
    staleTime: 60_000,
  });
}
