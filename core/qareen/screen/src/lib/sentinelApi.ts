// ---------------------------------------------------------------------------
// Sentinel API — typed wrappers for the autonomous agent monitor.
// ---------------------------------------------------------------------------

import { api } from "./api";

export type TriggerStatus =
  | "detected"
  | "spawning"
  | "researching"
  | "draft_ready"
  | "sending"
  | "sent"
  | "pending"
  | "blocked"
  | "failed"
  | "cancelled"
  | "discarded";

export type Confidence = "high" | "medium" | "low";

export interface Trigger {
  id: string;
  message_id: string;
  channel: string;
  trigger_phrase: string;
  status: TriggerStatus;
  task_inferred: string | null;
  confidence: Confidence | null;
  confidence_reasons: string[];
  contact_name: string | null;
  contact_handle: string | null;
  trigger_text: string;
  draft_preview: string | null;
  created_at: number;
  sent_at: number | null;
}

export interface ConversationTurn {
  direction: "in" | "out";
  text: string;
  timestamp: number;
}

export interface TriggerDetail extends Trigger {
  draft_body: string | null;
  frontmatter: Record<string, unknown>;
  conversation: ConversationTurn[];
  sources: string[];
  scope_reasoning?: string | null;
  error?: string | null;
  detected_at?: number | null;
  spawned_at?: number | null;
  researched_at?: number | null;
  drafted_at?: number | null;
}

export interface SentinelCounts {
  detected: number;
  spawning: number;
  researching: number;
  draft_ready: number;
  sending: number;
  sent: number;
  pending: number;
  blocked: number;
  failed: number;
  cancelled: number;
}

export interface SentinelStatus {
  enabled: boolean;
  paused: boolean;
  service: {
    running: boolean;
    pid: number;
    uptime_seconds: number;
  };
  watcher: {
    cursor: number;
  };
  counts_today: SentinelCounts;
  last_trigger: { id: string; created_at: number; status: TriggerStatus } | null;
  trigger_phrases: string[];
  channels: string[];
}

export interface TriggerListResponse {
  triggers: Trigger[];
}

const TERMINAL_STATUSES: TriggerStatus[] = [
  "sent",
  "pending",
  "blocked",
  "failed",
  "cancelled",
  "discarded",
];

export function isTerminal(status: TriggerStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

export function isInFlight(status: TriggerStatus): boolean {
  return !isTerminal(status);
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

export const sentinelApi = {
  status: () => api.get<SentinelStatus>("/sentinel/status"),

  triggers: (status?: TriggerStatus | "all") => {
    const q = status && status !== "all" ? `?status=${encodeURIComponent(status)}` : "";
    return api.get<TriggerListResponse>(`/sentinel/triggers${q}`);
  },

  trigger: (id: string) =>
    api.get<TriggerDetail>(`/sentinel/triggers/${encodeURIComponent(id)}`),

  cancel: (id: string) =>
    api.post<{ ok: boolean }>(`/sentinel/triggers/${encodeURIComponent(id)}/cancel`),

  send: (id: string) =>
    api.post<{ ok: boolean }>(`/sentinel/triggers/${encodeURIComponent(id)}/send`),

  discard: (id: string) =>
    api.post<{ ok: boolean }>(`/sentinel/triggers/${encodeURIComponent(id)}/discard`),

  pause: () => api.post<{ ok: boolean }>("/sentinel/pause"),

  resume: () => api.post<{ ok: boolean }>("/sentinel/resume"),
};

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

export function timeAgo(unix: number | null): string {
  if (!unix) return "—";
  const diff = Date.now() - unix * 1000;
  const secs = Math.floor(diff / 1000);
  if (secs < 1) return "just now";
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function formatTimestamp(unix: number | null): string {
  if (!unix) return "—";
  return new Date(unix * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h ${m}m`;
  }
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  return `${d}d ${h}h`;
}

// ---------------------------------------------------------------------------
// Display mappings
// ---------------------------------------------------------------------------

export function statusColor(
  status: TriggerStatus,
): "green" | "yellow" | "red" | "gray" | "blue" | "orange" | "purple" {
  switch (status) {
    case "sent":
      return "green";
    case "pending":
      return "yellow";
    case "failed":
      return "red";
    case "blocked":
      return "gray";
    case "cancelled":
    case "discarded":
      return "gray";
    case "draft_ready":
      return "purple";
    case "sending":
      return "orange";
    case "researching":
    case "spawning":
      return "blue";
    case "detected":
      return "blue";
    default:
      return "gray";
  }
}

export function statusLabel(status: TriggerStatus): string {
  switch (status) {
    case "draft_ready":
      return "Draft ready";
    default:
      return status.charAt(0).toUpperCase() + status.slice(1);
  }
}

export function confidenceColor(
  c: Confidence | null,
): "green" | "yellow" | "red" | "gray" {
  if (c === "high") return "green";
  if (c === "medium") return "yellow";
  if (c === "low") return "red";
  return "gray";
}

// Step progression (for in-flight rows)
const STEP_ORDER: TriggerStatus[] = [
  "detected",
  "spawning",
  "researching",
  "draft_ready",
  "sending",
];

export function stepIndex(status: TriggerStatus): number {
  const idx = STEP_ORDER.indexOf(status);
  return idx === -1 ? 0 : idx;
}

export const STEP_COUNT = STEP_ORDER.length;
