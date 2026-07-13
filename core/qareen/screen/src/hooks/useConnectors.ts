/**
 * useConnectors — Fetches the full connector list with status and metadata.
 * Used by the connector catalog to show all available services.
 */
import { useQuery } from '@tanstack/react-query';

export interface ConnectorInfo {
  id: string;
  name: string;
  icon: string;
  color: string;
  type: string;
  tier: number;
  category: string;
  description: string;
  status: 'connected' | 'partial' | 'available' | 'broken' | 'unavailable';
  status_detail: string;
  capabilities: { id: string; label: string; description: string }[];
  health: { name: string; status: string; detail: string; description?: string }[];
  automation_ideas: { id: string; name: string; description: string }[];
  accounts: string[];
  n8n: { credential_types?: string[]; node_types?: string[] };
}

interface ConnectorsSummary {
  total: number;
  connected: number;
  partial: number;
  available: number;
}

export function useConnectors() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['connectors'],
    queryFn: async (): Promise<{ connectors: ConnectorInfo[]; summary: ConnectorsSummary }> => {
      const res = await fetch('/api/connectors');
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json();
    },
    staleTime: 30_000,
  });

  return {
    connectors: data?.connectors ?? [],
    summary: data?.summary ?? { total: 0, connected: 0, partial: 0, available: 0 },
    isLoading,
    error,
    refresh: refetch,
  };
}
