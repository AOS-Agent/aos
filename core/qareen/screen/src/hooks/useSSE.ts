import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useRealtimeStore } from '@/store/realtime';

const SSE_URL = '/api/stream';

export function useSSE() {
  const queryClient = useQueryClient();
  const addEvent = useRealtimeStore((s) => s.addEvent);
  const setConnected = useRealtimeStore((s) => s.setConnected);
  const retryCount = useRef(0);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    function connect() {
      let es: EventSource;
      try {
        es = new EventSource(SSE_URL);
      } catch {
        return;
      }
      eventSourceRef.current = es;

      es.onopen = () => {
        setConnected(true);
        retryCount.current = 0;
      };

      es.addEventListener('activity', (e) => {
        try {
          const data = JSON.parse(e.data);
          addEvent({
            id: data.id ?? crypto.randomUUID(),
            type: 'activity',
            source: data.source ?? 'sse',
            message: data.message ?? '',
            data,
            timestamp: data.timestamp ?? new Date().toISOString(),
          });
        } catch {}
      });

      es.addEventListener('work', (e) => {
        queryClient.invalidateQueries({ queryKey: ['work'] });
        try {
          const data = JSON.parse(e.data);
          addEvent({
            id: data.id ?? crypto.randomUUID(),
            type: 'work_update',
            source: data.source ?? 'work',
            message: data.message ?? '',
            data,
            timestamp: data.timestamp ?? new Date().toISOString(),
          });
        } catch {}
      });

      es.addEventListener('health', (e) => {
        queryClient.invalidateQueries({ queryKey: ['services'] });
        try {
          const data = JSON.parse(e.data);
          addEvent({
            id: data.id ?? crypto.randomUUID(),
            type: 'health',
            source: data.source ?? 'health',
            message: data.message ?? '',
            data,
            timestamp: data.timestamp ?? new Date().toISOString(),
          });
        } catch {}
      });

      es.addEventListener('services', (e) => {
        queryClient.invalidateQueries({ queryKey: ['services'] });
        try {
          const data = JSON.parse(e.data);
          addEvent({
            id: data.id ?? crypto.randomUUID(),
            type: 'services',
            source: 'services',
            message: '',
            data,
            timestamp: new Date().toISOString(),
          });
        } catch {}
      });

      es.addEventListener('execution', (e) => {
        queryClient.invalidateQueries({ queryKey: ['executions'] });
        try {
          const data = JSON.parse(e.data);
          addEvent({
            id: data.id ?? crypto.randomUUID(),
            type: 'execution',
            source: data.agent_id ?? 'execution',
            message: `${data.agent_id ?? 'unknown'} → ${data.provider}/${data.model} (${data.status})`,
            data,
            timestamp: data.timestamp ?? new Date().toISOString(),
          });
        } catch {}
      });

      es.onerror = () => {
        setConnected(false);
        es.close();
        eventSourceRef.current = null;

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
