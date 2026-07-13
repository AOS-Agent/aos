import { useEffect, useState } from "react";
import {
  X,
  ArrowDownLeft,
  ArrowUpRight,
  Loader2,
  AlertCircle,
  FileText,
  Brain,
  Quote,
  Tag as TagIcon,
  Link2,
} from "lucide-react";
import { Tag, StatusDot } from "@/components/primitives";
import type { Trigger, TriggerDetail, ConversationTurn } from "@/lib/sentinelApi";
import {
  sentinelApi,
  statusColor,
  statusLabel,
  confidenceColor,
  formatTimestamp,
  timeAgo,
} from "@/lib/sentinelApi";

interface Props {
  trigger: Trigger;
  onClose: () => void;
}

export function TriggerDetailSheet({ trigger, onClose }: Props) {
  const [detail, setDetail] = useState<TriggerDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    sentinelApi
      .trigger(trigger.id)
      .then((d) => {
        if (!cancelled) {
          setDetail(d);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError(true);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [trigger.id]);

  // Close on Escape
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const t: TriggerDetail | Trigger = detail ?? trigger;

  return (
    <div
      className="fixed inset-0 z-50"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
      <div
        className="absolute right-0 top-0 bottom-0 w-full max-w-[560px] bg-bg-panel overflow-y-auto animate-[slideInRight_180ms_ease-out]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-6 sm:p-8">
          {/* Close */}
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">
                Trigger detail
              </span>
              <span className="text-[10px] font-mono text-text-quaternary">
                {t.id.slice(0, 12)}
              </span>
            </div>
            <button
              onClick={onClose}
              className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-hover text-text-quaternary cursor-pointer"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Header: contact + status */}
          <div className="mb-6">
            <h2 className="text-[20px] font-[650] text-text tracking-[-0.02em] mb-2 leading-tight">
              {t.contact_name || t.contact_handle || "Unknown contact"}
            </h2>
            <div className="flex items-center gap-2.5 flex-wrap">
              <Tag label={t.channel} color="gray" size="sm" />
              <Tag label={t.trigger_phrase} color="orange" size="sm" />
              <StatusDot
                color={statusColor(t.status)}
                size="sm"
                label={statusLabel(t.status)}
              />
              {t.confidence && (
                <StatusDot
                  color={confidenceColor(t.confidence)}
                  size="sm"
                  label={t.confidence}
                />
              )}
              <span className="text-[10px] text-text-quaternary font-mono">
                {timeAgo(t.created_at)}
              </span>
            </div>
          </div>

          {/* Trigger message — prominent quote */}
          <div className="mb-6 border-l-2 border-accent pl-4 py-1 bg-accent-subtle/30 rounded-r-[5px]">
            <div className="flex items-center gap-1.5 text-[10px] font-[590] uppercase tracking-wider text-text-quaternary mb-1.5">
              <Quote className="w-3 h-3" />
              Original trigger message
            </div>
            <p className="text-[15px] text-text italic leading-[1.6] font-serif">
              "{t.trigger_text}"
            </p>
          </div>

          {/* Inferred task */}
          <Section title="Inferred task" icon={<Brain className="w-3.5 h-3.5" />}>
            {t.task_inferred ? (
              <p className="text-[13.5px] text-text-secondary leading-[1.6]">
                {t.task_inferred}
              </p>
            ) : (
              <p className="text-[12px] text-text-quaternary italic">
                No task inferred yet
              </p>
            )}
          </Section>

          {/* Loading detail */}
          {loading && !detail && (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-5 h-5 text-text-quaternary animate-spin" />
            </div>
          )}

          {error && !detail && (
            <div className="flex items-center gap-2 text-[12px] text-red bg-red-muted rounded-[5px] px-4 py-3 mb-6">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>Failed to load full detail. Showing summary only.</span>
            </div>
          )}

          {/* Detail-only sections */}
          {detail && (
            <>
              {/* Conversation context */}
              {detail.conversation && detail.conversation.length > 0 && (
                <Section
                  title="Conversation Sentinel saw"
                  icon={<Link2 className="w-3.5 h-3.5" />}
                >
                  <div className="space-y-2 bg-bg-tertiary/30 rounded-[5px] p-3">
                    {detail.conversation.map((turn, i) => (
                      <ConversationRow key={i} turn={turn} />
                    ))}
                  </div>
                </Section>
              )}

              {/* Full draft body */}
              {detail.draft_body && (
                <Section
                  title="Full draft body"
                  icon={<FileText className="w-3.5 h-3.5" />}
                >
                  <div className="bg-bg-tertiary/40 rounded-[5px] p-4 whitespace-pre-wrap text-[13px] text-text-secondary leading-[1.6] font-serif">
                    {detail.draft_body}
                  </div>
                </Section>
              )}

              {/* Sources */}
              {detail.sources && detail.sources.length > 0 && (
                <Section title="Sources" icon={<TagIcon className="w-3.5 h-3.5" />}>
                  <ul className="space-y-1">
                    {detail.sources.map((s, i) => (
                      <li
                        key={i}
                        className="text-[12px] text-text-tertiary font-mono leading-[1.5] flex gap-2"
                      >
                        <span className="text-text-quaternary shrink-0">·</span>
                        <span className="break-all">{s}</span>
                      </li>
                    ))}
                  </ul>
                </Section>
              )}

              {/* Confidence reasons */}
              {t.confidence_reasons.length > 0 && (
                <Section
                  title="Confidence reasons"
                  icon={<Brain className="w-3.5 h-3.5" />}
                >
                  <ul className="space-y-1.5">
                    {t.confidence_reasons.map((r, i) => (
                      <li
                        key={i}
                        className="text-[12px] text-text-tertiary leading-[1.5] flex gap-2"
                      >
                        <span className="text-text-quaternary shrink-0">·</span>
                        <span>{r}</span>
                      </li>
                    ))}
                  </ul>
                </Section>
              )}

              {/* Scope reasoning */}
              {detail.scope_reasoning && (
                <Section
                  title="Scope reasoning"
                  icon={<Brain className="w-3.5 h-3.5" />}
                >
                  <p className="text-[12.5px] text-text-tertiary leading-[1.6] italic">
                    {detail.scope_reasoning}
                  </p>
                </Section>
              )}

              {/* Frontmatter */}
              {detail.frontmatter && Object.keys(detail.frontmatter).length > 0 && (
                <Section
                  title="Frontmatter"
                  icon={<FileText className="w-3.5 h-3.5" />}
                >
                  <div className="bg-bg-tertiary/40 rounded-[5px] p-3 space-y-1">
                    {Object.entries(detail.frontmatter).map(([k, v]) => (
                      <div key={k} className="flex gap-3 text-[11.5px]">
                        <span className="text-text-quaternary font-mono shrink-0 min-w-[100px]">
                          {k}
                        </span>
                        <span className="text-text-tertiary font-mono break-all">
                          {typeof v === "string" ? v : JSON.stringify(v)}
                        </span>
                      </div>
                    ))}
                  </div>
                </Section>
              )}

              {/* Timestamps */}
              <Section title="Timeline" icon={<FileText className="w-3.5 h-3.5" />}>
                <div className="space-y-1.5">
                  <TimelineRow
                    label="Detected"
                    timestamp={detail.detected_at ?? detail.created_at}
                  />
                  {detail.spawned_at && (
                    <TimelineRow label="Spawned" timestamp={detail.spawned_at} />
                  )}
                  {detail.researched_at && (
                    <TimelineRow
                      label="Researched"
                      timestamp={detail.researched_at}
                    />
                  )}
                  {detail.drafted_at && (
                    <TimelineRow label="Drafted" timestamp={detail.drafted_at} />
                  )}
                  {detail.sent_at && (
                    <TimelineRow label="Sent" timestamp={detail.sent_at} />
                  )}
                </div>
              </Section>

              {/* Error */}
              {detail.error && (
                <div className="mt-6 flex items-start gap-2 text-[12px] text-red bg-red-muted rounded-[5px] px-4 py-3 border border-red/20">
                  <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                  <div>
                    <div className="font-[590] mb-1">Error</div>
                    <p className="leading-[1.5] font-mono">{detail.error}</p>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <style>{`
        @keyframes slideInRight {
          from { transform: translateX(100%); }
          to { transform: translateX(0); }
        }
      `}</style>
    </div>
  );
}

/* ── Helpers ──────────────────────────────────────────────────────────── */

function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-6">
      <div className="flex items-center gap-1.5 mb-2">
        <span className="text-text-quaternary">{icon}</span>
        <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">
          {title}
        </span>
      </div>
      {children}
    </div>
  );
}

function ConversationRow({ turn }: { turn: ConversationTurn }) {
  const isOut = turn.direction === "out";
  return (
    <div className={`flex gap-2 ${isOut ? "flex-row-reverse" : ""}`}>
      <div
        className={`shrink-0 w-5 h-5 rounded-full flex items-center justify-center ${
          isOut ? "bg-accent/15 text-accent" : "bg-blue/15 text-blue"
        }`}
      >
        {isOut ? (
          <ArrowUpRight className="w-3 h-3" />
        ) : (
          <ArrowDownLeft className="w-3 h-3" />
        )}
      </div>
      <div className={`flex-1 min-w-0 ${isOut ? "text-right" : ""}`}>
        <p className="text-[12px] text-text-secondary leading-[1.5] font-serif">
          {turn.text}
        </p>
        <span className="text-[10px] text-text-quaternary font-mono">
          {formatTimestamp(turn.timestamp)}
        </span>
      </div>
    </div>
  );
}

function TimelineRow({
  label,
  timestamp,
}: {
  label: string;
  timestamp: number;
}) {
  return (
    <div className="flex items-center gap-3 text-[12px]">
      <span className="text-text-quaternary w-24 shrink-0">{label}</span>
      <span className="text-text-secondary font-mono">
        {formatTimestamp(timestamp)}
      </span>
      <span className="text-text-quaternary text-[10px] font-mono">
        {timeAgo(timestamp)}
      </span>
    </div>
  );
}
