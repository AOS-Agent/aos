// ---------------------------------------------------------------------------
// useRemoteAccess — data layer for the Cloudflare Remote Access wizard.
//
//   useRemoteAccessStatus()   polls GET /api/remote-access/status (faster while
//                             provisioning, slow once settled).
//   useValidateToken()        POST /validate-token — checks scopes, lists zones.
//   useConnect()              POST /connect — kicks off background provisioning.
//   useDisconnect()           POST /disconnect — tears everything down.
//   useRemoteAccessProgress() dedicated EventSource('/api/stream') listening for
//                             'remote_access.progress', accumulating steps in
//                             local state. It does NOT use the global useSSE
//                             store (that only handles activity/work/health).
//                             Reconnects with exponential backoff because the
//                             connector rebind briefly restarts qareen and drops
//                             the socket mid-provision.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

const API = '/api/remote-access';
const STREAM_URL = '/api/stream';
const PROGRESS_EVENT = 'remote_access.progress';
const STATUS_KEY = ['remote-access', 'status'] as const;

// ── Types ──

export interface RAZone {
  id: string;
  name: string;
}

export interface RAConnectorHealth {
  tunnel: string;
  dns: string;
  access: string;
  overall: string;
}

export interface RAStatus {
  status: 'connected' | 'provisioning' | 'disconnected' | 'error';
  hostname: string | null;
  domain: string | null;
  allowed_emails: string[];
  connector_health: RAConnectorHealth | null;
  error_message: string | null;
}

export interface RAProgress {
  step: string;
  status: 'in_progress' | 'done' | 'error';
  message: string;
  detail?: string;
}

export interface ValidateTokenResult {
  ok: boolean;
  account_id?: string;
  zones?: RAZone[];
  missing_scopes?: string[];
  error?: string;
}

export interface ConnectRequest {
  token: string;
  domain: string;
  hostname: string;
  zone_id: string;
  account_id: string;
  allowed_emails: string[];
}

// ── Status (polling) ──

export function useRemoteAccessStatus() {
  return useQuery({
    queryKey: STATUS_KEY,
    queryFn: async (): Promise<RAStatus> => {
      const res = await fetch(`${API}/status`);
      if (!res.ok) throw new Error(`Remote access status failed: ${res.status}`);
      return res.json();
    },
    staleTime: 5_000,
    // Poll quickly while provisioning so the UI flips promptly; back off once
    // the state has settled.
    refetchInterval: (query) =>
      query.state.data?.status === 'provisioning' ? 2_000 : 15_000,
  });
}

// ── Mutations ──

export function useValidateToken() {
  return useMutation({
    mutationFn: async (token: string): Promise<ValidateTokenResult> => {
      const res = await fetch(`${API}/validate-token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      });
      // A failed validation still returns 200 with { ok:false, missing_scopes }.
      // Only throw on a transport / server error.
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error ?? `Token validation failed: ${res.status}`);
      }
      return res.json();
    },
  });
}

export function useConnect() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (req: ConnectRequest): Promise<{ started: boolean }> => {
      const res = await fetch(`${API}/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(req),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error ?? `Connect failed: ${res.status}`);
      }
      return res.json();
    },
    // Provisioning runs as a background task; flip status to 'provisioning'.
    onSuccess: () => qc.invalidateQueries({ queryKey: STATUS_KEY }),
  });
}

export function useDisconnect() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<RAStatus> => {
      const res = await fetch(`${API}/disconnect`, { method: 'POST' });
      if (!res.ok) throw new Error(`Disconnect failed: ${res.status}`);
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: STATUS_KEY }),
  });
}

// ── Live provisioning progress (dedicated SSE) ──

export function useRemoteAccessProgress(): { steps: RAProgress[]; reset: () => void } {
  const [steps, setSteps] = useState<RAProgress[]>([]);
  const qc = useQueryClient();
  const esRef = useRef<EventSource | null>(null);
  const retryCount = useRef(0);

  const reset = useCallback(() => setSteps([]), []);

  useEffect(() => {
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      if (cancelled) return;
      let es: EventSource;
      try {
        es = new EventSource(STREAM_URL);
      } catch {
        return;
      }
      esRef.current = es;

      es.onopen = () => {
        retryCount.current = 0;
      };

      es.addEventListener(PROGRESS_EVENT, (e: MessageEvent) => {
        let p: RAProgress;
        try {
          p = JSON.parse(e.data) as RAProgress;
        } catch {
          return;
        }
        // Upsert by step so each step renders once and advances its status
        // (in_progress → done / error) instead of duplicating.
        setSteps((prev) => {
          const idx = prev.findIndex((s) => s.step === p.step);
          if (idx === -1) return [...prev, p];
          const next = prev.slice();
          next[idx] = p;
          return next;
        });
        // On a terminal step, refresh status so the UI moves to connected/error.
        if (p.step === 'complete' || p.status === 'error') {
          qc.invalidateQueries({ queryKey: STATUS_KEY });
        }
      });

      es.onerror = () => {
        // The rebind step restarts qareen and drops this socket — reconnect with
        // exponential backoff rather than giving up. Accumulated steps are kept;
        // the status poll is the safety net for the final 'complete'.
        es.close();
        esRef.current = null;
        if (cancelled) return;
        const delay = Math.min(1000 * Math.pow(2, retryCount.current), 30000);
        retryCount.current += 1;
        retryTimer = setTimeout(connect, delay);
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      esRef.current?.close();
      esRef.current = null;
    };
  }, [qc]);

  return { steps, reset };
}
