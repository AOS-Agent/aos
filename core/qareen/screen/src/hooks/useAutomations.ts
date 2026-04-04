/**
 * Automation hooks — React Query hooks for n8n-powered automations.
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

// ── Types ──

export interface N8nAutomation {
  id: string;
  name: string;
  description: string;
  user_prompt: string;
  recipe_id: string | null;
  n8n_workflow_id: string | null;
  status: 'draft' | 'active' | 'paused' | 'error' | 'archived';
  trigger_type: string;
  trigger_config: Record<string, unknown>;
  credentials_used: string[];
  last_run_at: string | null;
  last_run_status: string | null;
  run_count: number;
  error_message: string | null;
  variables: Record<string, unknown>;
  tags: string[];
  created_at: string;
  activated_at: string | null;
}

export interface GenerateResult {
  success: boolean;
  workflow_json: Record<string, unknown> | null;
  recipe_id: string | null;
  recipe_name: string | null;
  variables_used: Record<string, unknown>;
  human_summary: string;
  validation_errors: string[];
  clarification_needed: string | null;
  trigger_type: string;
  trigger_config: Record<string, unknown>;
}

export interface Recipe {
  id: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  required_credentials: string[];
  variables: {
    name: string;
    description: string;
    type: string;
    required: boolean;
    default: unknown;
    examples: string[];
  }[];
}

export interface AutomationsHealth {
  status: string;
  n8n?: Record<string, unknown>;
  active_workflows?: number;
  total_workflows?: number;
  message?: string;
}

// ── Hooks ──

export function useN8nAutomations() {
  return useQuery({
    queryKey: ['n8n-automations'],
    queryFn: async (): Promise<{ automations: N8nAutomation[]; count: number }> => {
      const res = await fetch('/api/automations/n8n');
      if (!res.ok) throw new Error(`API error: ${res.status}`);
      return res.json();
    },
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

export function useAutomationsHealth() {
  return useQuery({
    queryKey: ['automations-health'],
    queryFn: async (): Promise<AutomationsHealth> => {
      const res = await fetch('/api/automations/health');
      if (!res.ok) return { status: 'error', message: 'API unavailable' };
      return res.json();
    },
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

export function useRecipes() {
  return useQuery({
    queryKey: ['automation-recipes'],
    queryFn: async (): Promise<{ recipes: Recipe[]; count: number }> => {
      const res = await fetch('/api/automations/recipes');
      if (!res.ok) throw new Error(`API error: ${res.status}`);
      return res.json();
    },
    staleTime: 300_000, // Recipes don't change often
  });
}

export function useGenerateAutomation() {
  return useMutation({
    mutationFn: async (params: {
      description: string;
      connected_accounts?: string[];
      context?: Record<string, unknown>;
    }): Promise<GenerateResult> => {
      const res = await fetch('/api/automations/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      });
      if (!res.ok) throw new Error(`API error: ${res.status}`);
      return res.json();
    },
  });
}

export function useDeployAutomation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (params: {
      name: string;
      description?: string;
      user_prompt: string;
      recipe_id?: string;
      workflow_json: Record<string, unknown>;
      variables?: Record<string, unknown>;
      trigger_type?: string;
      trigger_config?: Record<string, unknown>;
      activate?: boolean;
    }) => {
      const res = await fetch('/api/automations/deploy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      });
      if (!res.ok) throw new Error(`Deploy failed: ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['n8n-automations'] });
      qc.invalidateQueries({ queryKey: ['automations'] });
      qc.invalidateQueries({ queryKey: ['automations-health'] });
    },
  });
}

export function useActivateAutomation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`/api/automations/${id}/activate`, { method: 'POST' });
      if (!res.ok) throw new Error(`Activate failed: ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['n8n-automations'] });
    },
  });
}

export function useDeactivateAutomation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`/api/automations/${id}/deactivate`, { method: 'POST' });
      if (!res.ok) throw new Error(`Deactivate failed: ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['n8n-automations'] });
    },
  });
}

export function useExecutionHistory(automationId: string | null) {
  return useQuery({
    queryKey: ['execution-history', automationId],
    queryFn: async () => {
      if (!automationId) return { executions: [], count: 0 };
      const res = await fetch(`/api/automations/${automationId}/executions`);
      if (!res.ok) throw new Error(`API error: ${res.status}`);
      return res.json();
    },
    enabled: !!automationId,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}
