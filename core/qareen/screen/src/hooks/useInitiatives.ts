import { useQuery } from '@tanstack/react-query';

const API = '/api';

/** One row from GET /api/initiatives — frontmatter-only summary. */
export interface InitiativeSummary {
  slug: string;
  title: string;
  status: string | null;
  stage: number | string | null;
  appetite: string | null;
  /** The `project` frontmatter slug (e.g. 'aos', 'deenoverdunya'). Maps to a work area. */
  project: string | null;
  tags: string[];
  date: string | null;
  updated: string | null;
}

export interface InitiativeListResponse {
  initiatives: InitiativeSummary[];
  total: number;
}

/** Full doc from GET /api/initiatives/{slug} — frontmatter + rendered markdown body. */
export interface InitiativeDoc {
  slug: string;
  title: string;
  status: string | null;
  stage: number | string | null;
  appetite: string | null;
  project: string | null;
  tags: string[];
  frontmatter: Record<string, unknown>;
  content: string;
}

async function fetchInitiatives(): Promise<InitiativeListResponse> {
  const res = await fetch(`${API}/initiatives`);
  if (!res.ok) throw new Error(`Initiatives API error: ${res.status}`);
  const raw = await res.json();
  const initiatives = Array.isArray(raw.initiatives) ? (raw.initiatives as InitiativeSummary[]) : [];
  return {
    initiatives,
    total: typeof raw.total === 'number' ? raw.total : initiatives.length,
  };
}

async function fetchInitiative(slug: string): Promise<InitiativeDoc> {
  const res = await fetch(`${API}/initiatives/${encodeURIComponent(slug)}`);
  if (!res.ok) throw new Error(`Initiative API error: ${res.status}`);
  const raw = await res.json();
  if (raw && raw.error) throw new Error(String(raw.error));
  return raw as InitiativeDoc;
}

export function useInitiatives() {
  return useQuery({
    queryKey: ['initiatives'],
    queryFn: fetchInitiatives,
    staleTime: 60_000,
    refetchInterval: 300_000,
  });
}

export function useInitiative(slug: string | null | undefined) {
  return useQuery({
    queryKey: ['initiative', slug],
    enabled: !!slug,
    staleTime: 60_000,
    queryFn: () => fetchInitiative(slug!),
  });
}
