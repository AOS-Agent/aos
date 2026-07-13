import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useMemo } from 'react';

// Minimal local stand-in for the provider registry (core/infra/models),
// which ships in a later wave. Model picker falls back to harness defaults
// until then — see useProviderModels() below.
interface Provider {
  id: string;
  type: string;
  display_name: string;
  status: string;
  models: string[];
}

const API = '/api';

export interface AgentConfig {
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
  source: string;
  default_trust: number;
  is_system: boolean;
  is_active: boolean;
  schedule: Record<string, string>;
  // Extended config
  permission_mode: string;
  max_turns: number | null;
  effort: string;
  can_spawn: string[];
  disallowed_tools: string[];
  isolation: string;
  background: boolean;
  memory: string;
  rules: string[];
  parameters: Record<string, string>;
  services: string[];
  prerequisites: string[];
  on_failure: string;
  max_retries: number;
  inputs: string[];
  outputs: string[];
  self_contained: boolean;
  version: string;
  body: string;
}

export interface AgentOptions {
  tools: string[];
  skills: string[];
  mcpServers: string[];
  rules: string[];
  services: string[];
  agents: string[];
}

export interface HealthCheck {
  agent_id: string;
  healthy: boolean;
  checks: { type: string; name: string; ok: boolean; message: string }[];
}

// ── Fetchers ──

async function fetchAgentConfig(id: string): Promise<AgentConfig> {
  const res = await fetch(`${API}/agents/${encodeURIComponent(id)}/config`);
  if (!res.ok) throw new Error(`Config API error: ${res.status}`);
  return res.json();
}

async function patchAgentConfig(id: string, patch: Partial<AgentConfig>): Promise<AgentConfig> {
  const res = await fetch(`${API}/agents/${encodeURIComponent(id)}/config`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error ?? `Save failed: ${res.status}`);
  }
  return res.json();
}

async function fetchOptionList(endpoint: string): Promise<string[]> {
  const res = await fetch(`${API}/agents/options/${endpoint}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.items ?? [];
}

async function fetchAgentHealth(id: string): Promise<HealthCheck> {
  const res = await fetch(`${API}/agents/${encodeURIComponent(id)}/health`);
  if (!res.ok) throw new Error(`Health API error: ${res.status}`);
  return res.json();
}

// ── Hooks ──

/** Full agent config including body */
export function useAgentConfig(id: string | null) {
  return useQuery({
    queryKey: ['agents', id, 'config'],
    queryFn: () => fetchAgentConfig(id!),
    enabled: !!id,
    staleTime: 10_000,
  });
}

/** PATCH mutation for saving config */
export function useSaveConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Partial<AgentConfig> }) =>
      patchAgentConfig(id, patch),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: ['agents'] });
      qc.invalidateQueries({ queryKey: ['agents', id, 'config'] });
    },
  });
}

/** Fetches all option lists in parallel */
export function useAgentOptions() {
  const tools = useQuery({
    queryKey: ['agents', 'options', 'tools'],
    queryFn: () => fetchOptionList('tools'),
    staleTime: 60_000,
  });
  const skills = useQuery({
    queryKey: ['agents', 'options', 'skills'],
    queryFn: () => fetchOptionList('skills'),
    staleTime: 60_000,
  });
  const mcpServers = useQuery({
    queryKey: ['agents', 'options', 'mcp-servers'],
    queryFn: () => fetchOptionList('mcp-servers'),
    staleTime: 60_000,
  });
  const rules = useQuery({
    queryKey: ['agents', 'options', 'rules'],
    queryFn: () => fetchOptionList('rules'),
    staleTime: 60_000,
  });
  const services = useQuery({
    queryKey: ['agents', 'options', 'services'],
    queryFn: () => fetchOptionList('services'),
    staleTime: 60_000,
  });
  const agents = useQuery({
    queryKey: ['agents', 'options', 'agents'],
    queryFn: () => fetchOptionList('agents'),
    staleTime: 60_000,
  });

  return {
    tools: tools.data ?? [],
    skills: skills.data ?? [],
    mcpServers: mcpServers.data ?? [],
    rules: rules.data ?? [],
    services: services.data ?? [],
    agents: agents.data ?? [],
    isLoading: tools.isLoading || skills.isLoading || mcpServers.isLoading ||
      rules.isLoading || services.isLoading || agents.isLoading,
  };
}

/** Health check for a single agent */
export function useAgentHealth(id: string | null) {
  return useQuery({
    queryKey: ['agents', id, 'health'],
    queryFn: () => fetchAgentHealth(id!),
    enabled: !!id,
    staleTime: 30_000,
  });
}

// ── Provider-aware model picker ──

export interface ModelOption {
  value: string;        // e.g. "sonnet" or "openrouter/google/gemini-2.5-pro"
  label: string;        // e.g. "Claude Code → Sonnet"
  provider: string;     // e.g. "Claude Code"
  providerType: string; // e.g. "harness"
}

/** Provider types that offer selectable LLM models */
const MODEL_PROVIDER_TYPES = new Set(['harness', 'api', 'gateway', 'local']);

function buildModelOptions(providers: Provider[]): ModelOption[] {
  const options: ModelOption[] = [];

  for (const p of providers) {
    if (p.status !== 'active') continue;
    if (!MODEL_PROVIDER_TYPES.has(p.type)) continue;
    if (p.models.length === 0) continue;

    for (const model of p.models) {
      // Harness models stay as bare aliases (opus, sonnet, haiku)
      // Other providers prefix with provider id
      const value = p.type === 'harness' ? model : `${p.id}/${model}`;
      const label = `${p.display_name} \u2192 ${model}`;

      options.push({ value, label, provider: p.display_name, providerType: p.type });
    }
  }

  return options;
}

/** Flat list of model options grouped by active provider, with "inherit" first. */
export function useProviderModels(): ModelOption[] {
  // Provider registry ships in a later wave — always fall back to harness defaults.
  const providers: Provider[] = [];

  return useMemo(() => {
    const inherit: ModelOption = {
      value: 'inherit',
      label: 'Inherit (parent model)',
      provider: '',
      providerType: '',
    };

    if (!providers || providers.length === 0) {
      // Fallback: at minimum show harness defaults so the picker is never empty
      return [
        inherit,
        { value: 'opus', label: 'Opus', provider: 'Claude Code', providerType: 'harness' },
        { value: 'sonnet', label: 'Sonnet', provider: 'Claude Code', providerType: 'harness' },
        { value: 'haiku', label: 'Haiku', provider: 'Claude Code', providerType: 'harness' },
      ];
    }

    return [inherit, ...buildModelOptions(providers)];
  }, [providers]);
}
