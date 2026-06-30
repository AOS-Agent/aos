import { useState } from "react";
import {
  Check,
  X as XIcon,
  ChevronDown,
  Loader2,
  AlertCircle,
  ArrowRight,
} from "lucide-react";
import type { Trigger } from "@/lib/sentinelApi";
import {
  statusColor,
  statusLabel,
  confidenceColor,
  timeAgo,
} from "@/lib/sentinelApi";

interface Props {
  trigger: Trigger;
  onSelect: () => void;
  onApprove?: (id: string) => Promise<void>;
  onDiscard?: (id: string) => Promise<void>;
}

const STATUS_DOT: Record<string, string> = {
  green: "bg-green",
  yellow: "bg-yellow",
  red: "bg-red",
  blue: "bg-blue",
  orange: "bg-orange",
  purple: "bg-purple",
  gray: "bg-text-quaternary",
  teal: "bg-teal",
};

const STATUS_TEXT: Record<string, string> = {
  green: "text-green",
  yellow: "text-yellow",
  red: "text-red",
  blue: "text-blue",
  orange: "text-orange",
  purple: "text-purple",
  gray: "text-text-tertiary",
  teal: "text-teal",
};

export function TriggerCard({ trigger, onSelect, onApprove, onDiscard }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [acting, setActing] = useState<"approve" | "discard" | null>(null);

  const isPending = trigger.status === "pending";
  const isFailed = trigger.status === "failed";
  const isBlocked = trigger.status === "blocked";

  const sColor = statusColor(trigger.status);
  const cColor = confidenceColor(trigger.confidence);
  const sLabel = statusLabel(trigger.status);

  async function handleApprove(e: React.MouseEvent) {
    e.stopPropagation();
    if (!onApprove) return;
    setActing("approve");
    try {
      await onApprove(trigger.id);
    } finally {
      setActing(null);
    }
  }

  async function handleDiscard(e: React.MouseEvent) {
    e.stopPropagation();
    if (!onDiscard) return;
    setActing("discard");
    try {
      await onDiscard(trigger.id);
    } finally {
      setActing(null);
    }
  }

  const borderClass = isPending
    ? "border-yellow/25"
    : isFailed
      ? "border-red/20"
      : isBlocked
        ? "border-orange/20"
        : "border-border-secondary";

  return (
    <div
      className={`group relative rounded-[10px] bg-bg-secondary border transition-colors cursor-pointer ${borderClass} hover:border-border-tertiary`}
      onClick={onSelect}
      style={{ transitionDuration: "var(--duration-instant)" }}
    >
      {/* Pending left accent rail */}
      {isPending && (
        <div className="absolute left-0 top-3 bottom-3 w-[3px] rounded-r-full bg-yellow/70" />
      )}

      <div className="px-7 py-6">
        {/* Header — contact + status + time */}
        <div className="flex items-center justify-between gap-4 mb-5">
          <div className="flex items-center gap-3 min-w-0">
            <span className="text-[14px] font-[560] text-text tracking-[-0.005em] truncate">
              {trigger.contact_name || trigger.contact_handle || "Unknown"}
            </span>
            <span className="font-mono text-[10px] text-text-tertiary uppercase tracking-[0.08em]">
              {trigger.channel}
            </span>
          </div>
          <div className="flex items-center gap-4 shrink-0">
            {trigger.confidence && (
              <span className="flex items-center gap-1.5">
                <span className={`w-1.5 h-1.5 rounded-full ${STATUS_DOT[cColor]}`} />
                <span className={`text-[11px] tracking-[0.005em] ${STATUS_TEXT[cColor]}`}>
                  {trigger.confidence}
                </span>
              </span>
            )}
            <span className="flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full ${STATUS_DOT[sColor]}`} />
              <span className={`text-[11px] tracking-[0.005em] ${STATUS_TEXT[sColor]}`}>
                {sLabel}
              </span>
            </span>
            <span className="text-[10px] text-text-quaternary font-mono">
              {timeAgo(trigger.created_at)}
            </span>
          </div>
        </div>

        {/* Trigger → Task — the heroes */}
        <div className="space-y-4">
          {/* The trigger message — biggest, serif, accent rail */}
          <div className="border-l-[2px] border-accent/70 pl-5">
            <div className="text-[10px] font-[590] uppercase tracking-[0.12em] text-accent/80 mb-1.5">
              You said
            </div>
            <p className="font-serif text-[18px] leading-[1.4] text-text italic tracking-[-0.005em]">
              {trigger.trigger_text
                ? `“${trigger.trigger_text}”`
                : "(no text captured)"}
            </p>
          </div>

          {/* Arrow + inferred task */}
          <div className="flex items-start gap-3 pl-5">
            <ArrowRight className="w-3.5 h-3.5 text-text-quaternary mt-1.5 shrink-0" />
            <div className="min-w-0">
              <div className="text-[10px] font-[590] uppercase tracking-[0.12em] text-text-quaternary mb-1">
                Sentinel inferred
              </div>
              {trigger.task_inferred ? (
                <p className="text-[14px] text-text-secondary leading-[1.5]">
                  {trigger.task_inferred}
                </p>
              ) : (
                <p className="text-[13px] text-text-quaternary italic">No task inferred yet</p>
              )}
            </div>
          </div>
        </div>

        {/* Expandable details */}
        {(trigger.draft_preview || trigger.confidence_reasons.length > 0) && (
          <>
            <button
              onClick={(e) => {
                e.stopPropagation();
                setExpanded((v) => !v);
              }}
              className="mt-5 flex items-center gap-1.5 text-[11px] font-[510] text-text-tertiary hover:text-text-secondary transition-colors cursor-pointer"
              style={{ transitionDuration: "var(--duration-instant)" }}
            >
              <ChevronDown
                className={`w-3 h-3 transition-transform ${expanded ? "" : "-rotate-90"}`}
                style={{ transitionDuration: "var(--duration-instant)" }}
              />
              {expanded ? "Hide details" : "Show draft & reasons"}
            </button>

            {expanded && (
              <div className="mt-4 space-y-4 pt-4 border-t border-border">
                {trigger.draft_preview && (
                  <div>
                    <div className="text-[10px] font-[590] uppercase tracking-[0.12em] text-text-quaternary mb-2">
                      Draft preview
                    </div>
                    <p className="font-serif text-[14px] text-text-secondary leading-[1.5] bg-bg-tertiary/60 rounded-[7px] px-4 py-3 italic">
                      “{trigger.draft_preview}”
                    </p>
                  </div>
                )}
                {trigger.confidence_reasons.length > 0 && (
                  <div>
                    <div className="text-[10px] font-[590] uppercase tracking-[0.12em] text-text-quaternary mb-2">
                      Held back because
                    </div>
                    <ul className="space-y-1.5">
                      {trigger.confidence_reasons.map((r, i) => (
                        <li
                          key={i}
                          className="text-[12px] text-text-tertiary leading-[1.55] flex gap-2.5"
                        >
                          <span className="text-yellow/60 shrink-0 mt-[5px]">•</span>
                          <span>{r}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {/* Failure indicator */}
        {isFailed && (
          <div className="mt-5 flex items-center gap-2.5 text-[12px] text-red bg-red-muted/60 rounded-[5px] px-3 py-2 border border-red/15">
            <AlertCircle className="w-3.5 h-3.5 shrink-0" />
            <span>Send failed — open for error details</span>
          </div>
        )}

        {/* Actions for pending */}
        {(isPending || isBlocked) && onApprove && onDiscard && (
          <div className="mt-6 pt-5 border-t border-border flex items-center justify-end gap-2">
            <button
              onClick={handleDiscard}
              disabled={!!acting}
              className="h-9 px-4 rounded-[7px] flex items-center gap-2 text-text-tertiary hover:text-red hover:bg-red-muted/60 border border-transparent hover:border-red/20 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              style={{ transitionDuration: "var(--duration-instant)" }}
            >
              {acting === "discard" ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <XIcon className="w-3.5 h-3.5" />
              )}
              <span className="text-[12px] font-[560]">Discard</span>
            </button>
            <button
              onClick={handleApprove}
              disabled={!!acting}
              className="h-9 px-4 rounded-[7px] flex items-center gap-2 bg-green-muted text-green border border-green/30 hover:bg-green/15 hover:border-green/50 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              style={{ transitionDuration: "var(--duration-instant)" }}
            >
              {acting === "approve" ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Check className="w-3.5 h-3.5" />
              )}
              <span className="text-[12px] font-[560]">Approve & send</span>
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
