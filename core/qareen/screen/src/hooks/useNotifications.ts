import { useEffect, useRef } from 'react';
import { useRealtimeStore, type SystemEvent } from '@/store/realtime';

type ServiceState = Record<string, { status: string; detail: string }>;

const BATCH_WINDOW_MS = 3000;

/**
 * Watches realtime events and fires browser Notification API
 * for service outages and task lifecycle changes.
 */
export function useNotifications() {
  const events = useRealtimeStore((s) => s.events);
  const prevServicesRef = useRef<ServiceState | null>(null);
  const lastEventIdRef = useRef<string | null>(null);
  const batchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingDownRef = useRef<string[]>([]);
  const permissionRef = useRef<boolean>(false);

  // Request notification permission once
  useEffect(() => {
    if ('Notification' in window) {
      if (Notification.permission === 'granted') {
        permissionRef.current = true;
      } else if (Notification.permission !== 'denied') {
        Notification.requestPermission().then(result => {
          permissionRef.current = result === 'granted';
        });
      }
    }
  }, []);

  // React to new events
  useEffect(() => {
    if (!events.length) return;
    const latest = events[0];
    if (!latest || latest.id === lastEventIdRef.current) return;
    lastEventIdRef.current = latest.id;
    handleEvent(latest);
  }, [events]);

  function handleEvent(event: SystemEvent) {
    if (!permissionRef.current) return;

    if (event.type === 'services' && event.data) {
      handleServicesChange(event.data as ServiceState);
    }

    if (event.type === 'work_update' && event.data) {
      handleWorkUpdate(event.data);
    }
  }

  function handleServicesChange(current: ServiceState) {
    const prev = prevServicesRef.current;
    prevServicesRef.current = current;
    if (!prev) return;

    const newlyDown: string[] = [];
    for (const [name, info] of Object.entries(current)) {
      const wasOnline = prev[name]?.status === 'online';
      const isOffline = info.status === 'offline';
      if (wasOnline && isOffline) {
        newlyDown.push(name);
      }
    }

    if (!newlyDown.length) return;

    pendingDownRef.current.push(...newlyDown);
    if (batchTimerRef.current) clearTimeout(batchTimerRef.current);
    batchTimerRef.current = setTimeout(flushServiceNotifications, BATCH_WINDOW_MS);
  }

  function flushServiceNotifications() {
    const services = [...new Set(pendingDownRef.current)];
    pendingDownRef.current = [];
    batchTimerRef.current = null;

    if (!services.length) return;

    const body = services.length === 1
      ? `${services[0]} went offline`
      : `${services.length} services went offline: ${services.join(', ')}`;

    new Notification('AOS Service Alert', { body });
  }

  function handleWorkUpdate(data: Record<string, unknown>) {
    const action = data.action as string;
    const title = data.title as string;
    const taskId = data.task_id as string;

    if (action === 'task_done' && title) {
      new Notification('Task Completed', { body: `${taskId}: ${title}` });
    } else if (action === 'task_created' && title) {
      new Notification('New Task', { body: `${taskId}: ${title}` });
    }
  }
}
