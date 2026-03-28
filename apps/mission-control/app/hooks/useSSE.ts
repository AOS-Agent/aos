'use client';

import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useRealtimeStore } from '@/store/realtime';

const SSE_URL = '/api/stream';

/**
 * Connects to the dashboard SSE stream and invalidates
 * TanStack Query caches when relevant events arrive.
 *
 * Events from the stream:
 * - activity: new agent activity entry
 * - health: service health snapshot
 * - work_update: task/project changed
 * - services: service status changed
 */
export function useSSE() {
  const queryClient = useQueryClient();
  const addEvent = useRealtimeStore((s) => s.addEvent);
  const setConnected = useRealtimeStore((s) => s.setConnected);
  const retryCount = useRef(0);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    // SSE only works when the dashboard stream is available
    // Silently degrade to polling if not
    if (typeof window === 'undefined') return;

    function connect() {
      let es: EventSource;
      try {
        es = new EventSource(SSE_URL);
      } catch {
        return; // SSE not supported or URL unreachable
      }
      eventSourceRef.current = es;

      es.onopen = () => {
        setConnected(true);
        retryCount.current = 0;
      };

      es.addEventListener('activity', (e) => {
        try {
          const data = JSON.parse(e.data);
          addEvent({ type: 'activity', data, timestamp: Date.now() });
        } catch {}
      });

      es.addEventListener('work_update', (e) => {
        // Invalidate work queries — tasks, projects, inbox will refetch
        queryClient.invalidateQueries({ queryKey: ['work'] });
        try {
          const data = JSON.parse(e.data);
          addEvent({ type: 'work_update', data, timestamp: Date.now() });
        } catch {}
      });

      es.addEventListener('health', (e) => {
        queryClient.invalidateQueries({ queryKey: ['services'] });
        try {
          const data = JSON.parse(e.data);
          addEvent({ type: 'health', data, timestamp: Date.now() });
        } catch {}
      });

      es.addEventListener('services', (e) => {
        queryClient.invalidateQueries({ queryKey: ['services'] });
      });

      es.onerror = () => {
        setConnected(false);
        es.close();
        eventSourceRef.current = null;

        // Exponential backoff: 1s, 2s, 4s, 8s, max 30s
        const delay = Math.min(1000 * Math.pow(2, retryCount.current), 30000);
        retryCount.current++;
        setTimeout(connect, delay);
      };
    }

    connect();

    return () => {
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
      setConnected(false);
    };
  }, [queryClient, addEvent, setConnected]);
}
