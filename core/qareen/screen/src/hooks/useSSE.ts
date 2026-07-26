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
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;

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
        // Phase 2: the open card's narrative timeline + any single-task fetch.
        queryClient.invalidateQueries({ queryKey: ['activity'] });
        queryClient.invalidateQueries({ queryKey: ['task'] });
        // The brief recompiles on every task mutation. Refreshing here as well
        // as on project.brief.updated means the project page stays live even
        // on a machine where the compiler's own event isn't wired yet.
        queryClient.invalidateQueries({ queryKey: ['project-brief'] });
      };

      const WORK_EVENTS = [
        'work', 'work.notify', 'ingest.work',
        'task.created', 'task.updated', 'task.completed',
        'task.deleted', 'task.status_changed', 'task.delegated', 'task.activity',
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

      // Project briefs — the compiler re-emits this on every recompile
      // (task mutation, session close, explicit recompile). Invalidating the
      // single project's cache entry is what makes the project page update
      // itself while the operator watches. Payload: {project_id, state,
      // compiled_at}. If the id is missing, refresh every brief rather than
      // silently dropping the event.
      es.addEventListener('project.brief.updated', (e) => {
        let projectId: string | null = null;
        try {
          projectId = JSON.parse((e as MessageEvent).data)?.project_id ?? null;
        } catch {
          // Malformed payload — fall through to refreshing every brief.
        }
        queryClient.invalidateQueries({
          queryKey: projectId ? ['project-brief', projectId] : ['project-brief'],
        });
      });

      // Shipments — Auto Tracker bus events keep the board, detail page,
      // approval queue, and eval queue live.
      const invalidateShipments = () => {
        queryClient.invalidateQueries({ queryKey: ['shipments'] });
        queryClient.invalidateQueries({ queryKey: ['shipment'] });
        queryClient.invalidateQueries({ queryKey: ['shipment-candidates'] });
        queryClient.invalidateQueries({ queryKey: ['shipment-domain-rules'] });
        queryClient.invalidateQueries({ queryKey: ['shipment-eval'] });
      };
      const SHIPMENT_EVENTS = ['shipment.updated', 'shipment.milestone', 'shipment.candidate'];
      for (const name of SHIPMENT_EVENTS) {
        es.addEventListener(name, () => invalidateShipments());
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

        if (cancelled) return;
        const delay = Math.min(1000 * Math.pow(2, retryCount.current), 30000);
        retryCount.current++;
        retryTimer.current = setTimeout(connect, delay);
      };
    }

    connect();

    return () => {
      // Cancel a pending reconnect as well as the live stream. Without this,
      // a retry scheduled before unmount still fires and opens an EventSource
      // that nothing ever closes — each leak permanently occupies one of the
      // browser's six HTTP/1.1 sockets for this origin, which is a budget the
      // route chunks and every /api call have to share.
      cancelled = true;
      if (retryTimer.current) clearTimeout(retryTimer.current);
      retryTimer.current = null;
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
      setConnected(false);
    };
  }, [queryClient, addEvent, setConnected]);
}
