import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useConverseStore } from '@/store/converse';
import { actionsKey, sessionKey } from '@/hooks/useConverse';

// ---------------------------------------------------------------------------
// useConverseStream — SSE subscription for GET /api/converse/stream.
//
// Mirrors useNotificationStream.ts's per-feature-EventSource pattern (own
// endpoint, own listener set) rather than folding into the global useSSE.ts
// bus — converse events only matter while the /converse screen is open, and
// this endpoint is a distinct stream from the global /api/stream bus, so a
// page-scoped subscription (mounted from pages/Converse.tsx) keeps a
// localhost-only tool lean instead of polling comms.db from every screen.
//
// Reconnect: exponential backoff capped at 30s, same shape as useSSE.ts.
// Server sends `: connected` + periodic `: heartbeat N` comments which
// EventSource ignores by default — no listener needed for those.
// ---------------------------------------------------------------------------

const STREAM_URL = '/api/converse/stream';

interface SessionCreatedPayload {
  id: string;
}
interface SessionStatePayload {
  id: string;
}
interface MessageAddedPayload {
  session_id: string;
}
interface ActionPayload {
  id: string;
  session_id: string;
}

export function useConverseStream() {
  const queryClient = useQueryClient();
  const setConnected = useConverseStore((s) => s.setConnected);
  const touchSession = useConverseStore((s) => s.touchSession);
  const retryCount = useRef(0);
  const eventSourceRef = useRef<EventSource | null>(null);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;

    function connect() {
      let es: EventSource;
      try {
        es = new EventSource(STREAM_URL);
      } catch {
        return;
      }
      eventSourceRef.current = es;

      es.onopen = () => {
        setConnected(true);
        retryCount.current = 0;
      };

      es.addEventListener('session_created', (e) => {
        try {
          const data = JSON.parse((e as MessageEvent).data) as SessionCreatedPayload;
          touchSession(data.id);
        } catch {
          // fall through — still refresh the list below
        }
        queryClient.invalidateQueries({ queryKey: ['converse-sessions'] });
      });

      es.addEventListener('session_state', (e) => {
        try {
          const data = JSON.parse((e as MessageEvent).data) as SessionStatePayload;
          touchSession(data.id);
          queryClient.invalidateQueries({ queryKey: sessionKey(data.id) });
        } catch {
          // malformed payload — still refresh the list
        }
        queryClient.invalidateQueries({ queryKey: ['converse-sessions'] });
      });

      es.addEventListener('message_added', (e) => {
        try {
          const data = JSON.parse((e as MessageEvent).data) as MessageAddedPayload;
          touchSession(data.session_id);
          queryClient.invalidateQueries({ queryKey: sessionKey(data.session_id) });
          // The list shows "last message" previews — keep it fresh too.
          queryClient.invalidateQueries({ queryKey: ['converse-sessions'] });
        } catch {
          // ignore malformed payload
        }
      });

      es.addEventListener('action_proposed', (e) => {
        queryClient.invalidateQueries({ queryKey: actionsKey() });
        try {
          const data = JSON.parse((e as MessageEvent).data) as ActionPayload;
          touchSession(data.session_id);
          queryClient.invalidateQueries({ queryKey: sessionKey(data.session_id) });
        } catch {
          // ignore malformed payload
        }
      });

      es.addEventListener('action_decided', (e) => {
        queryClient.invalidateQueries({ queryKey: actionsKey() });
        try {
          const data = JSON.parse((e as MessageEvent).data) as ActionPayload;
          touchSession(data.session_id);
          queryClient.invalidateQueries({ queryKey: sessionKey(data.session_id) });
        } catch {
          // ignore malformed payload
        }
      });

      es.onerror = () => {
        setConnected(false);
        es.close();
        eventSourceRef.current = null;

        if (cancelled) return;
        const delay = Math.min(1000 * Math.pow(2, retryCount.current), 30000);
        retryCount.current++;
        retryTimer.current = setTimeout(connect, delay);
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (retryTimer.current) clearTimeout(retryTimer.current);
      retryTimer.current = null;
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
      setConnected(false);
    };
  }, [queryClient, setConnected, touchSession]);
}

// Re-exported for components that only need the connection dot, without
// depending on react-query.
export function useConverseConnected(): boolean {
  return useConverseStore((s) => s.connected);
}
