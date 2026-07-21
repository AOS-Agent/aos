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

      // Invalidate the work queries so the board live-updates. The bus emits
      // task.* (API/agent path), work.notify (re-emit), and ingest.work (CLI
      // path via /api/work/notify) — none of which matched the old 'work'
      // listener, so the kanban never refreshed from agent or CLI activity.
      const invalidateWork = () => {
        queryClient.invalidateQueries({ queryKey: ['work'] });
        queryClient.invalidateQueries({ queryKey: ['project-tasks'] });
      };

      const WORK_EVENTS = [
        'work', 'work.notify', 'ingest.work',
        'task.created', 'task.updated', 'task.completed',
        'task.deleted', 'task.status_changed', 'task.delegated',
      ];
      for (const name of WORK_EVENTS) {
        es.addEventListener(name, (e) => {
          invalidateWork();
          try {
            const data = JSON.parse((e as MessageEvent).data);
            addEvent({
              id: data.id ?? crypto.randomUUID(),
              type: 'work_update',
              source: data.source ?? name,
              message: data.message ?? '',
              data,
              timestamp: data.timestamp ?? new Date().toISOString(),
            });
          } catch {}
        });
      }

      // Belt-and-suspenders: any event delivered without a matching named
      // listener (no `event:` field) still refreshes work if it looks work-ish.
      es.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          const t = String(data.type ?? data.event_type ?? '');
          if (t.startsWith('task') || t.includes('work')) invalidateWork();
        } catch {}
      };

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
