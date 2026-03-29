'use client';

import { useEffect, useRef } from 'react';
import { useRealtimeStore, type SystemEvent } from '@/store/realtime';

type ServiceState = Record<string, { status: string; detail: string }>;

// Batch window: collect rapid-fire events before sending one notification
const BATCH_WINDOW_MS = 3000;

/**
 * Watches realtime events and fires native macOS notifications
 * for service outages and task lifecycle changes.
 *
 * Uses @tauri-apps/plugin-notification (loaded dynamically to
 * avoid breaking in non-Tauri environments like plain Next.js dev).
 */
export function useNotifications() {
  const events = useRealtimeStore((s) => s.events);
  const prevServicesRef = useRef<ServiceState | null>(null);
  const lastEventIdRef = useRef<string | null>(null);
  const batchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingDownRef = useRef<string[]>([]);
  const notifModuleRef = useRef<typeof import('@tauri-apps/plugin-notification') | null>(null);
  const permissionRef = useRef<boolean>(false);

  // Load the notification module and request permission once
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mod = await import('@tauri-apps/plugin-notification');
        if (cancelled) return;
        notifModuleRef.current = mod;

        let granted = await mod.isPermissionGranted();
        if (!granted) {
          const result = await mod.requestPermission();
          granted = result === 'granted';
        }
        permissionRef.current = granted;
      } catch {
        // Not in Tauri environment — notifications unavailable
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // React to new events
  useEffect(() => {
    if (!events.length) return;
    const latest = events[0]; // events are newest-first
    if (!latest || latest.id === lastEventIdRef.current) return;
    lastEventIdRef.current = latest.id;

    if (latest.type === 'health') {
      // health events contain services in the same SSE tick — skip,
      // we handle service transitions from the 'services' type in the store
      // (services events are separate)
    }

    // Detect service state transitions
    if (latest.type === 'health' && latest.data) {
      // The services SSE event gets added to the store as type 'health'
      // by useSSE — but actually services come as separate events.
      // Let's handle this in a unified way below.
    }

    handleEvent(latest);
  }, [events]);

  function handleEvent(event: SystemEvent) {
    if (!permissionRef.current || !notifModuleRef.current) return;

    if (event.data && isServicesPayload(event.data)) {
      handleServicesChange(event.data as ServiceState);
    }

    if (event.type === 'work_update' && event.data) {
      handleWorkUpdate(event.data);
    }
  }

  function isServicesPayload(data: Record<string, unknown>): boolean {
    // Services payloads have service names as keys with {status, detail} values
    const values = Object.values(data);
    if (!values.length) return false;
    const first = values[0];
    return typeof first === 'object' && first !== null && 'status' in first && 'detail' in first;
  }

  function handleServicesChange(current: ServiceState) {
    const prev = prevServicesRef.current;
    prevServicesRef.current = current;

    // Skip first event — no previous state to compare
    if (!prev) return;

    // Find services that transitioned to offline
    const newlyDown: string[] = [];
    for (const [name, info] of Object.entries(current)) {
      const wasOnline = prev[name]?.status === 'online';
      const isOffline = info.status === 'offline';
      if (wasOnline && isOffline) {
        newlyDown.push(name);
      }
    }

    if (!newlyDown.length) return;

    // Batch: accumulate and send after window
    pendingDownRef.current.push(...newlyDown);
    if (batchTimerRef.current) clearTimeout(batchTimerRef.current);
    batchTimerRef.current = setTimeout(flushServiceNotifications, BATCH_WINDOW_MS);
  }

  function flushServiceNotifications() {
    const mod = notifModuleRef.current;
    const services = [...new Set(pendingDownRef.current)];
    pendingDownRef.current = [];
    batchTimerRef.current = null;

    if (!mod || !services.length) return;

    const body = services.length === 1
      ? `${services[0]} went offline`
      : `${services.length} services went offline: ${services.join(', ')}`;

    mod.sendNotification({ title: 'AOS Service Alert', body });
  }

  function handleWorkUpdate(data: Record<string, unknown>) {
    const mod = notifModuleRef.current;
    if (!mod) return;

    const action = data.action as string;
    const title = data.title as string;
    const taskId = data.task_id as string;

    if (action === 'task_done' && title) {
      mod.sendNotification({
        title: 'Task Completed',
        body: `${taskId}: ${title}`,
      });
    } else if (action === 'task_created' && title) {
      mod.sendNotification({
        title: 'New Task',
        body: `${taskId}: ${title}`,
      });
    }
  }
}
