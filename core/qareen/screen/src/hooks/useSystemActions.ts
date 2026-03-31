import { useMutation, useQueryClient } from '@tanstack/react-query';

const API = '/api';

export function useRestartService() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (name: string) => {
      const res = await fetch(`${API}/automations/launchagent/${name}/restart`, { method: 'POST' });
      if (!res.ok) throw new Error(`Restart failed: ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ['services'] });
        qc.invalidateQueries({ queryKey: ['attention'] });
      }, 3000);
    },
  });
}

export function useRunCron() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (name: string) => {
      const res = await fetch(`${API}/crons/${name}/run`, { method: 'POST' });
      if (!res.ok) throw new Error(`Run failed: ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['crons'] });
      qc.invalidateQueries({ queryKey: ['attention'] });
    },
  });
}

export function useToggleCron() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ name, enabled }: { name: string; enabled: boolean }) => {
      const res = await fetch(`${API}/crons/${name}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      if (!res.ok) throw new Error(`Toggle failed: ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['crons'] });
      qc.invalidateQueries({ queryKey: ['attention'] });
    },
  });
}

export async function fetchCronOutput(name: string): Promise<{ name: string; lines: string[]; exists: boolean }> {
  const res = await fetch(`${API}/crons/${name}/output`);
  if (!res.ok) throw new Error(`Fetch output failed: ${res.status}`);
  return res.json();
}

export async function fetchServiceLogs(name: string): Promise<{ name: string; lines: string[]; exists: boolean }> {
  const res = await fetch(`${API}/services/${name}/logs`);
  if (!res.ok) throw new Error(`Fetch logs failed: ${res.status}`);
  return res.json();
}
