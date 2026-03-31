import { useQuery } from '@tanstack/react-query';

const API = '/api';

export interface AttentionItem {
  type: 'error' | 'warning';
  icon: string;
  text: string;
  detail: string;
  action_type?: 'restart_service' | 'run_cron';
  action_target?: string;
}

export interface AttentionData {
  verdict: 'healthy' | 'warning' | 'critical';
  verdict_text: string;
  summary: string;
  items: AttentionItem[];
}

async function fetchAttention(): Promise<AttentionData> {
  const res = await fetch(`${API}/system/attention`);
  if (!res.ok) throw new Error(`Attention API error: ${res.status}`);
  return res.json();
}

export function useAttention() {
  return useQuery({
    queryKey: ['attention'],
    queryFn: fetchAttention,
    refetchInterval: 30_000,
  });
}
