import { useEffect, useRef } from 'react';
import { Bell, X, Check } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNotificationList, type Notification } from '@/hooks/useNotificationStream';

// ---------------------------------------------------------------------------
// NotificationTray — dropdown panel from the bell icon.
// Shows recent notifications with read/unread state.
// Click a row to navigate. "Mark all read" at bottom.
// ---------------------------------------------------------------------------

const PRIORITY_COLORS: Record<string, string> = {
  low: '#6B6560',
  normal: '#D9730D',
  high: '#FFD60A',
  urgent: '#FF453A',
};

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return 'just now';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function NotificationRow({
  notification,
  onNavigate,
}: {
  notification: Notification;
  onNavigate: (n: Notification) => void;
}) {
  const accent = PRIORITY_COLORS[notification.priority] ?? PRIORITY_COLORS.normal;

  return (
    <button
      type="button"
      onClick={() => onNavigate(notification)}
      className="
        w-full flex items-start gap-2.5 px-3 py-2.5 text-left
        cursor-pointer transition-colors duration-100
        hover:bg-hover
      "
    >
      <div
        className="w-1.5 h-1.5 rounded-full shrink-0 mt-1.5"
        style={{ backgroundColor: notification.read ? 'transparent' : accent }}
      />
      <div className="flex-1 min-w-0">
        <span className={`text-[12px] font-[510] block ${notification.read ? 'text-text-tertiary' : 'text-text-secondary'}`}>
          {notification.title}
        </span>
        {notification.body && (
          <span className="text-[11px] text-text-quaternary block mt-0.5 line-clamp-1">
            {notification.body}
          </span>
        )}
      </div>
      <span className="text-[10px] text-text-quaternary shrink-0 mt-0.5">
        {timeAgo(notification.created_at)}
      </span>
    </button>
  );
}

export function NotificationTray({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { data: notifications, isLoading } = useNotificationList();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const trayRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (trayRef.current && !trayRef.current.contains(e.target as Node)) onClose();
    };
    // Delay to avoid catching the bell click
    const timer = setTimeout(() => document.addEventListener('mousedown', handler), 50);
    return () => { clearTimeout(timer); document.removeEventListener('mousedown', handler); };
  }, [open, onClose]);

  // Close on escape
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onClose]);

  const markAllRead = useMutation({
    mutationFn: async () => {
      await fetch('/api/notifications/read-all', { method: 'POST' });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['notification-list'] });
      qc.invalidateQueries({ queryKey: ['notification-unread'] });
    },
  });

  const handleNavigate = (n: Notification) => {
    // Mark read
    fetch(`/api/notifications/${n.id}/read`, { method: 'PATCH' }).catch(() => {});
    qc.invalidateQueries({ queryKey: ['notification-unread'] });
    qc.invalidateQueries({ queryKey: ['notification-list'] });
    onClose();
    if (n.action_url) navigate(n.action_url);
  };

  if (!open) return null;

  const items = notifications ?? [];
  const hasUnread = items.some((n) => !n.read);

  return (
    <div
      ref={trayRef}
      className="
        fixed top-12 right-3 z-[500]
        w-[340px] max-w-[calc(100vw-24px)] max-h-[420px]
        bg-bg-panel/95 backdrop-blur-xl
        border border-border/60
        rounded-[10px]
        shadow-[0_8px_32px_rgba(0,0,0,0.5)]
        flex flex-col
        animate-[fadeIn_150ms_ease-out]
      "
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-border/40">
        <div className="flex items-center gap-2">
          <Bell className="w-3.5 h-3.5 text-text-tertiary" />
          <span className="text-[13px] font-[590] text-text-secondary">Notifications</span>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="p-1 rounded-[3px] text-text-quaternary hover:text-text-tertiary hover:bg-hover transition-colors cursor-pointer"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="px-3 py-6 text-center">
            <span className="text-[11px] text-text-quaternary">Loading...</span>
          </div>
        ) : items.length === 0 ? (
          <div className="px-3 py-8 text-center">
            <span className="text-[12px] text-text-quaternary">No notifications yet</span>
          </div>
        ) : (
          <div className="divide-y divide-border/40">
            {items.map((n) => (
              <NotificationRow
                key={n.id}
                notification={n}
                onNavigate={handleNavigate}
              />
            ))}
          </div>
        )}
      </div>

      {/* Footer */}
      {hasUnread && (
        <div className="border-t border-border/40 px-3 py-2">
          <button
            type="button"
            onClick={() => markAllRead.mutate()}
            className="
              flex items-center gap-1.5 w-full justify-center
              text-[11px] font-[510] text-text-quaternary
              hover:text-text-tertiary cursor-pointer
              transition-colors duration-100
            "
          >
            <Check className="w-3 h-3" />
            Mark all read
          </button>
        </div>
      )}

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(-4px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}
