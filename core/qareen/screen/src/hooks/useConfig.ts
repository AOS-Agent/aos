import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type {
  OperatorResponse,
  UpdateOperatorRequest,
  AccountsResponse,
  IntegrationsResponse,
} from '@/lib/types';

const API = '/api';

export function useOperator() {
  return useQuery({
    queryKey: ['operator'],
    queryFn: async (): Promise<OperatorResponse> => {
      const res = await fetch(`${API}/config/operator`);
      if (!res.ok) throw new Error(`Operator API error: ${res.status}`);
      return res.json();
    },
    staleTime: 300_000,
  });
}

export function useUpdateOperator() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: UpdateOperatorRequest) => {
      const res = await fetch(`${API}/config/operator`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error(`Update operator failed: ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['operator'] });
    },
  });
}

export function useAccounts() {
  return useQuery({
    queryKey: ['accounts'],
    queryFn: async (): Promise<AccountsResponse> => {
      const res = await fetch(`${API}/config/accounts`);
      if (!res.ok) throw new Error(`Accounts API error: ${res.status}`);
      return res.json();
    },
    staleTime: 300_000,
  });
}

export function useIntegrations() {
  return useQuery({
    queryKey: ['integrations'],
    queryFn: async (): Promise<IntegrationsResponse> => {
      const res = await fetch(`${API}/config/integrations`);
      if (!res.ok) throw new Error(`Integrations API error: ${res.status}`);
      return res.json();
    },
    staleTime: 120_000,
  });
}
