// ---------------------------------------------------------------------------
// Formatting utilities for the Qareen frontend
// ---------------------------------------------------------------------------

import { formatDistanceToNow, format, parseISO, differenceInSeconds } from 'date-fns';

// ---------------------------------------------------------------------------
// Date formatters
// ---------------------------------------------------------------------------

/**
 * Returns a human-readable relative time string (e.g., "3 minutes ago").
 */
export function timeAgo(dateStr: string | undefined | null): string {
  if (!dateStr) return '';
  try {
    return formatDistanceToNow(parseISO(dateStr), { addSuffix: true });
  } catch {
    return dateStr;
  }
}

/**
 * Format a date string to a specific display format.
 * Defaults to "MMM d, yyyy" (e.g., "Mar 30, 2026").
 */
export function formatDate(dateStr: string | undefined | null, fmt = 'MMM d, yyyy'): string {
  if (!dateStr) return '';
  try {
    return format(parseISO(dateStr), fmt);
  } catch {
    return dateStr;
  }
}

/**
 * Format a date as time only, e.g. "2:30 PM".
 */
export function formatTime(dateStr: string | undefined | null): string {
  if (!dateStr) return '';
  try {
    return format(parseISO(dateStr), 'h:mm a');
  } catch {
    return dateStr;
  }
}

/**
 * Format a date as "Mar 30, 2:30 PM".
 */
export function formatDateTime(dateStr: string | undefined | null): string {
  if (!dateStr) return '';
  try {
    return format(parseISO(dateStr), 'MMM d, h:mm a');
  } catch {
    return dateStr;
  }
}

// ---------------------------------------------------------------------------
// Duration formatters
// ---------------------------------------------------------------------------

/**
 * Format a duration in seconds to a human-readable string.
 * e.g., 90 => "1m 30s", 3700 => "1h 1m"
 */
export function formatDuration(seconds: number | undefined | null): string {
  if (seconds == null || seconds < 0) return '';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  if (mins < 60) return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
  const hrs = Math.floor(mins / 60);
  const remainMins = mins % 60;
  return remainMins > 0 ? `${hrs}h ${remainMins}m` : `${hrs}h`;
}

/**
 * Format elapsed time between two ISO timestamps.
 */
export function formatElapsed(start: string | undefined | null, end?: string | null): string {
  if (!start) return '';
  try {
    const startDate = parseISO(start);
    const endDate = end ? parseISO(end) : new Date();
    const secs = differenceInSeconds(endDate, startDate);
    return formatDuration(secs);
  } catch {
    return '';
  }
}

// ---------------------------------------------------------------------------
// Status label helpers
// ---------------------------------------------------------------------------

const STATUS_LABELS: Record<string, string> = {
  todo: 'To Do',
  active: 'Active',
  waiting: 'Waiting',
  done: 'Done',
  cancelled: 'Cancelled',
  blocked: 'Blocked',
  online: 'Online',
  offline: 'Offline',
  unknown: 'Unknown',
  ok: 'OK',
  failed: 'Failed',
  queued: 'Queued',
  processing: 'Processing',
  completed: 'Completed',
  escalated: 'Escalated',
  pending: 'Pending',
  approved: 'Approved',
  dismissed: 'Dismissed',
  expired: 'Expired',
};

/**
 * Returns a display-friendly label for a status string.
 */
export function statusLabel(status: string | undefined | null): string {
  if (!status) return '';
  return STATUS_LABELS[status] ?? status.charAt(0).toUpperCase() + status.slice(1);
}

const STATUS_COLORS: Record<string, string> = {
  active: 'green',
  online: 'green',
  ok: 'green',
  completed: 'green',
  approved: 'green',
  done: 'green',
  todo: 'blue',
  queued: 'blue',
  pending: 'yellow',
  waiting: 'yellow',
  processing: 'yellow',
  blocked: 'red',
  failed: 'red',
  cancelled: 'red',
  escalated: 'red',
  expired: 'gray',
  offline: 'gray',
  unknown: 'gray',
  dismissed: 'gray',
};

/**
 * Returns a status dot color suitable for StatusDot component.
 */
export function statusColor(status: string | undefined | null): string {
  if (!status) return 'gray';
  return STATUS_COLORS[status] ?? 'gray';
}

// ---------------------------------------------------------------------------
// Number formatters
// ---------------------------------------------------------------------------

/**
 * Compact number display, e.g. 1234 => "1.2k"
 */
export function compactNumber(n: number): string {
  if (n < 1000) return n.toString();
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

/**
 * Format bytes to human-readable size.
 */
export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}
