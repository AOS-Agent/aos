// ---------------------------------------------------------------------------
// Converse API — typed wrappers for the Conversation-Session engine.
//
// Converse unifies Sentinel (mode='sentinel', voice='operator') and Envoy
// (mode='envoy', voice='agent') onto one supervised runtime that holds
// goal-directed multi-turn conversations with real people over iMessage and
// Slack. This file mirrors core/qareen/api/converse.py's pydantic schemas
// field-for-field — that router is the single source of truth for shape.
//
// Known API scope limits (see converse.py module docstring), surfaced here
// so the UI never implies more than the backend does:
//   - inject(deliver='send') and actions/{id}/approve only *queue* an
//     outbound row (state='queued'). Nothing sends until the T3 daemon picks
//     it up — there is no daemon yet. The UI should read 'queued' as
//     "waiting for the converse service" not "delivered".
//   - There is no endpoint to change trust_level after creation — render it
//     read-only.
//   - resume() does not bump max_messages when leaving 'capped' (flagged
//     deviation in converse.py; the status transition still happens).
// ---------------------------------------------------------------------------

import { api } from "./api";

export type SessionMode = "sentinel" | "envoy";
export type SessionVoice = "operator" | "agent";
export type SessionChannel = "imessage" | "slack";
export type ToolsProfile = "none" | "research" | "full";

export type SessionStatus =
  | "active"
  | "handling"
  | "waiting"
  | "escalated"
  | "takeover"
  | "paused"
  | "complete"
  | "stopped"
  | "expired"
  | "capped"
  | "failed";

export const TERMINAL_STATUSES: SessionStatus[] = [
  "complete",
  "stopped",
  "expired",
  "capped",
  "failed",
];

export function isTerminalStatus(status: SessionStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

export type MessageRole = "contact" | "agent" | "operator" | "system";
export type MessageDirection = "inbound" | "outbound" | "internal";
export type MessageState =
  | "received"
  | "handling"
  | "handled"
  | "failed"
  | "queued"
  | "sent"
  | "send_failed"
  | "done";

export type ActionKind = "send_reply" | "human_touchpoint" | "close";
export type ActionStatus = "proposed" | "approved" | "rejected" | "executed" | "expired";

export interface Artifact {
  kind: string;
  url?: string;
  note?: string;
  [key: string]: unknown;
}

export interface ConverseSession {
  id: string;
  mode: SessionMode;
  voice: SessionVoice;
  channel: SessionChannel;
  conversation_ref: string;
  counterpart_handle: string;
  person_id: string | null;
  person_name: string | null;
  mission: string;
  success_criteria: string | null;
  constraints: string | null;
  tools: ToolsProfile;
  trust_level: number;
  status: SessionStatus;
  paused_reason: string | null;
  cursor: string | null;
  state_summary: string | null;
  artifacts: Artifact[];
  handling_started_at: number | null;
  turn_count: number;
  sent_count: number;
  error_count: number;
  max_messages: number;
  expires_at: number | null;
  origin: string | null;
  created_at: number;
  updated_at: number;
  closed_at: number | null;
  close_reason: string | null;
}

export interface ConverseMessage {
  id: string;
  session_id: string;
  channel_message_id: string | null;
  role: MessageRole;
  direction: MessageDirection;
  text: string;
  state: MessageState;
  attempt_count: number;
  error: string | null;
  ts: string; // ISO8601
  created_at: number; // epoch seconds
}

export interface ConverseAction {
  id: string;
  session_id: string;
  kind: ActionKind;
  payload: { text?: string; description?: string; artifact_url?: string; reason?: string; [k: string]: unknown };
  gate_reasons: string[];
  status: ActionStatus;
  created_at: number;
  decided_at: number | null;
  executed_at: number | null;
}

export interface SessionListResponse {
  sessions: ConverseSession[];
  total: number;
}

export interface SessionDetailResponse extends ConverseSession {
  messages: ConverseMessage[];
  pending_actions: ConverseAction[];
  workspace_path: string;
  log_path: string;
}

export interface ActionListResponse {
  actions: ConverseAction[];
  total: number;
}

export interface CreateSessionBody {
  mode: SessionMode;
  voice: SessionVoice;
  channel: SessionChannel;
  counterpart_handle: string;
  mission: string;
  conversation_ref?: string;
  person_id?: string;
  person_name?: string;
  success_criteria?: string;
  constraints?: string;
  tools?: ToolsProfile;
  trust_level?: number;
  max_messages?: number;
  expires_days?: number;
  state_summary?: string;
  artifacts?: Artifact[];
  origin?: string;
}

export interface InjectResult {
  ok: boolean;
  deliver: "note" | "send";
  message: ConverseMessage;
  message_id: string;
}

export interface ActionDecisionResponse {
  ok: boolean;
  action: ConverseAction;
  message_id: string | null;
}

export type SessionStatusFilter = "active" | "all" | SessionStatus;

export const converseApi = {
  sessions: (opts: { status?: SessionStatusFilter; channel?: string; mode?: string; limit?: number } = {}) => {
    const params = new URLSearchParams();
    if (opts.status) params.set("status", opts.status);
    if (opts.channel) params.set("channel", opts.channel);
    if (opts.mode) params.set("mode", opts.mode);
    if (opts.limit) params.set("limit", String(opts.limit));
    const qs = params.toString();
    return api.get<SessionListResponse>(`/converse/sessions${qs ? `?${qs}` : ""}`);
  },

  session: (id: string) =>
    api.get<SessionDetailResponse>(`/converse/sessions/${encodeURIComponent(id)}`),

  messages: (id: string, opts: { afterId?: string; limit?: number } = {}) => {
    const params = new URLSearchParams();
    if (opts.afterId) params.set("after_id", opts.afterId);
    if (opts.limit) params.set("limit", String(opts.limit));
    const qs = params.toString();
    return api.get<{ messages: ConverseMessage[] }>(
      `/converse/sessions/${encodeURIComponent(id)}/messages${qs ? `?${qs}` : ""}`,
    );
  },

  create: (body: CreateSessionBody) => api.post<ConverseSession>("/converse/sessions", body),

  pause: (id: string, reason?: string) =>
    api.post<ConverseSession>(`/converse/sessions/${encodeURIComponent(id)}/pause`, reason ? { reason } : {}),

  resume: (id: string) => api.post<ConverseSession>(`/converse/sessions/${encodeURIComponent(id)}/resume`),

  takeover: (id: string) => api.post<ConverseSession>(`/converse/sessions/${encodeURIComponent(id)}/takeover`),

  release: (id: string) => api.post<ConverseSession>(`/converse/sessions/${encodeURIComponent(id)}/release`),

  inject: (id: string, text: string, deliver: "note" | "send") =>
    api.post<InjectResult>(`/converse/sessions/${encodeURIComponent(id)}/inject`, { text, deliver }),

  close: (id: string, reason?: string) =>
    api.post<ConverseSession>(`/converse/sessions/${encodeURIComponent(id)}/close`, { reason }),

  pendingActions: (limit = 50) =>
    api.get<ActionListResponse>(`/converse/actions?status=proposed&limit=${limit}`),

  approveAction: (id: string) =>
    api.post<ActionDecisionResponse>(`/converse/actions/${encodeURIComponent(id)}/approve`),

  rejectAction: (id: string) =>
    api.post<ActionDecisionResponse>(`/converse/actions/${encodeURIComponent(id)}/reject`),
};

// ---------------------------------------------------------------------------
// Formatting / display helpers
// ---------------------------------------------------------------------------

export function timeAgo(unixSeconds: number | null): string {
  if (!unixSeconds) return "—";
  const diff = Date.now() - unixSeconds * 1000;
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

export function formatTimestamp(unixSeconds: number | null): string {
  if (!unixSeconds) return "—";
  return new Date(unixSeconds * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatMessageTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

type StatusColor = "green" | "yellow" | "red" | "orange" | "blue" | "purple" | "gray";

export function statusColor(status: SessionStatus): StatusColor {
  switch (status) {
    case "active":
      return "blue";
    case "handling":
      return "purple";
    case "waiting":
      return "yellow";
    case "escalated":
      return "red";
    case "takeover":
      return "orange";
    case "paused":
      return "gray";
    case "complete":
      return "green";
    case "capped":
      return "yellow";
    case "failed":
      return "red";
    case "stopped":
    case "expired":
    default:
      return "gray";
  }
}

export function statusLabel(status: SessionStatus): string {
  switch (status) {
    case "handling":
      return "Thinking";
    case "waiting":
      return "Waiting";
    case "takeover":
      return "Manual";
    default:
      return status.charAt(0).toUpperCase() + status.slice(1);
  }
}

export function modeLabel(mode: SessionMode): string {
  return mode === "sentinel" ? "Sentinel" : "Envoy";
}

export function modeColor(mode: SessionMode): "blue" | "purple" {
  return mode === "sentinel" ? "blue" : "purple";
}

export function channelLabel(channel: SessionChannel): string {
  return channel === "imessage" ? "iMessage" : "Slack";
}

export function displayName(session: Pick<ConverseSession, "person_name" | "counterpart_handle">): string {
  return session.person_name || session.counterpart_handle || "Unknown contact";
}
