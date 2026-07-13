import { useState, useEffect, useCallback, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

// ---------------------------------------------------------------------------
// Notification system — listens to SSE for live notifications,
// manages toast queue, provides unread count.
// ---------------------------------------------------------------------------

export interface Notification {
  id: string;
  type: string;
  title: string;
  body: string;
  priority: 'low' | 'normal' | 'high' | 'urgent';
  created_at: string;
  action_url?: string;
  read: boolean;
  channels?: string[];
}

const MAX_TOASTS = 3;
const TOAST_DURATION: Record<string, number> = {
  low: 4000,
  normal: 5000,
  high: 8000,
  urgent: 0, // persistent until dismissed
};

// ── Unread count hook ──

export function useUnreadCount() {
  return useQuery({
    queryKey: ['notification-unread'],
    queryFn: async (): Promise<number> => {
      const res = await fetch('/api/notifications/unread');
      if (!res.ok) return 0;
      const data = await res.json();
      return data.count ?? 0;
    },
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

// ── Notification list hook ──

export function useNotificationList() {
  return useQuery({
    queryKey: ['notification-list'],
    queryFn: async (): Promise<Notification[]> => {
      const res = await fetch('/api/notifications');
      if (!res.ok) return [];
      return res.json();
    },
    staleTime: 30_000,
  });
}

// ── Toast queue hook — manages visible toasts ──

export function useToastQueue() {
  const [toasts, setToasts] = useState<Notification[]>([]);
  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const addToast = useCallback((notif: Notification) => {
    setToasts((prev) => {
      // Already showing this one?
      if (prev.some((t) => t.id === notif.id)) return prev;
      // Max visible — drop oldest
      const next = [...prev, notif];
      if (next.length > MAX_TOASTS) next.shift();
      return next;
    });

    // Auto-dismiss
    const duration = TOAST_DURATION[notif.priority] ?? TOAST_DURATION.normal;
    if (duration > 0) {
      const timer = setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== notif.id));
        timers.current.delete(notif.id);
      }, duration);
      timers.current.set(notif.id, timer);
    }
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer) { clearTimeout(timer); timers.current.delete(id); }
  }, []);

  return { toasts, addToast, dismissToast };
}

// ── SSE listener for notification events ──

export function useNotificationSSE(onNotification: (n: Notification) => void) {
  const qc = useQueryClient();

  useEffect(() => {
    // Listen to the companion SSE stream for notification events
    const es = new EventSource('/companion/stream');

    es.addEventListener('notification', (event) => {
      try {
        const notif: Notification = JSON.parse(event.data);
        onNotification(notif);
        // Invalidate unread count + list
        qc.invalidateQueries({ queryKey: ['notification-unread'] });
        qc.invalidateQueries({ queryKey: ['notification-list'] });
      } catch {
        // Malformed event — ignore
      }
    });

    return () => es.close();
  }, [onNotification, qc]);
}
