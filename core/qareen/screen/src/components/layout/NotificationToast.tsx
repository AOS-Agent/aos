import { X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type { Notification } from '@/hooks/useNotificationStream';

// ---------------------------------------------------------------------------
// NotificationToast — slides in from top-right, warm glass style.
// Auto-dismisses based on priority. Click navigates to action_url.
// ---------------------------------------------------------------------------

const PRIORITY_ACCENT: Record<string, string> = {
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
  return `${hrs}h ago`;
}

export function NotificationToast({
  notification,
  onDismiss,
}: {
  notification: Notification;
  onDismiss: (id: string) => void;
}) {
  const navigate = useNavigate();
  const accent = PRIORITY_ACCENT[notification.priority] ?? PRIORITY_ACCENT.normal;

  const handleClick = () => {
    if (notification.action_url) {
      navigate(notification.action_url);
    }
    // Mark as read via API (fire and forget)
    fetch(`/api/notifications/${notification.id}/read`, { method: 'PATCH' }).catch(() => {});
    onDismiss(notification.id);
  };

  return (
    <div
      onClick={handleClick}
      className="
        w-[320px] p-3 rounded-[10px] cursor-pointer
        bg-bg-panel/95 backdrop-blur-xl
        border border-border/60
        shadow-[0_4px_24px_rgba(0,0,0,0.4)]
        animate-[slideInRight_220ms_ease-out]
      "
    >
      <div className="flex items-start gap-2.5">
        {/* Priority dot */}
        <div
          className="w-2 h-2 rounded-full shrink-0 mt-1.5"
          style={{ backgroundColor: accent }}
        />

        {/* Content */}
        <div className="flex-1 min-w-0">
          <span className="text-[13px] font-[510] text-text-secondary block">
            {notification.title}
          </span>
          {notification.body && (
            <span className="text-[11px] text-text-quaternary block mt-0.5 line-clamp-2">
              {notification.body}
            </span>
          )}
          <span className="text-[10px] text-text-quaternary block mt-1">
            {timeAgo(notification.created_at)}
          </span>
        </div>

        {/* Dismiss */}
        <button
          onClick={(e) => { e.stopPropagation(); onDismiss(notification.id); }}
          className="shrink-0 p-1 rounded-[3px] text-text-quaternary hover:text-text-tertiary hover:bg-hover transition-colors cursor-pointer"
        >
          <X className="w-3 h-3" />
        </button>
      </div>

      <style>{`
        @keyframes slideInRight {
          from { transform: translateX(100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>
    </div>
  );
}

// ── Toast container — stacks toasts in top-right ──

export function ToastContainer({
  toasts,
  onDismiss,
}: {
  toasts: Notification[];
  onDismiss: (id: string) => void;
}) {
  if (toasts.length === 0) return null;

  return (
    <div className="fixed top-3 right-3 z-[400] flex flex-col gap-2">
      {toasts.map((t) => (
        <NotificationToast key={t.id} notification={t} onDismiss={onDismiss} />
      ))}
    </div>
  );
}
