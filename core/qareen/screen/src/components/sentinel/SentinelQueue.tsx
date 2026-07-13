import { useState } from "react";
import { Loader2, X as XIcon, ChevronRight } from "lucide-react";
import type { Trigger } from "@/lib/sentinelApi";
import {
  isInFlight,
  statusColor,
  statusLabel,
  timeAgo,
  stepIndex,
  STEP_COUNT,
} from "@/lib/sentinelApi";

interface Props {
  triggers: Trigger[];
  onSelect: (t: Trigger) => void;
  onCancel: (id: string) => Promise<void>;
}

export function SentinelQueue({ triggers, onSelect, onCancel }: Props) {
  const live = triggers.filter((t) => isInFlight(t.status));

  return (
    <section>
      <div className="flex items-center justify-between mb-5">
        <h3 className="text-[11px] font-[590] uppercase tracking-[0.14em] text-text-quaternary">
          Live queue
        </h3>
        {live.length > 0 && (
          <span className="text-[10px] font-mono text-accent">
            {live.length} in flight
          </span>
        )}
      </div>

      {live.length === 0 ? (
        <div className="rounded-[10px] border border-border bg-bg/40 px-6 py-10 text-center">
          <p className="font-serif text-[15px] italic text-text-tertiary">
            Nothing in flight. Sentinel is watching.
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {live.map((t) => (
            <LiveRow
              key={t.id}
              trigger={t}
              onSelect={() => onSelect(t)}
              onCancel={() => onCancel(t.id)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function LiveRow({
  trigger,
  onSelect,
  onCancel,
}: {
  trigger: Trigger;
  onSelect: () => void;
  onCancel: () => void;
}) {
  const [cancelling, setCancelling] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const color = statusColor(trigger.status);
  const idx = stepIndex(trigger.status);

  const accentClass = {
    green: "bg-green",
    yellow: "bg-yellow",
    red: "bg-red",
    gray: "bg-text-quaternary",
    blue: "bg-blue",
    orange: "bg-orange",
    purple: "bg-purple",
    teal: "bg-teal",
  }[color];

  async function handleCancel(e: React.MouseEvent) {
    e.stopPropagation();
    if (!confirmCancel) {
      setConfirmCancel(true);
      setTimeout(() => setConfirmCancel(false), 3000);
      return;
    }
    setCancelling(true);
    try {
      await onCancel();
    } finally {
      setCancelling(false);
      setConfirmCancel(false);
    }
  }

  return (
    <div
      className="group rounded-[10px] bg-bg-secondary border border-border-secondary hover:border-border-tertiary transition-colors cursor-pointer"
      onClick={onSelect}
      style={{ transitionDuration: "var(--duration-instant)" }}
    >
      <div className="px-6 py-5">
        <div className="flex items-center gap-4 mb-4">
          <span className="relative inline-flex items-center justify-center w-3 h-3 shrink-0">
            <span className={`w-2 h-2 rounded-full ${accentClass} animate-pulse`} />
            <span className={`absolute inset-0 rounded-full ${accentClass} opacity-30 animate-ping`} />
          </span>

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 flex-wrap">
              <span className="text-[14px] font-[560] text-text tracking-[-0.005em] truncate">
                {trigger.contact_name || trigger.contact_handle || "Unknown contact"}
              </span>
              <span className="font-mono text-[10px] text-accent border border-accent/40 rounded-[4px] px-1.5 py-0.5">
                {trigger.trigger_phrase}
              </span>
            </div>
            <p className="text-[11.5px] text-text-tertiary mt-1">
              {statusLabel(trigger.status)} · {timeAgo(trigger.created_at)}
            </p>
          </div>

          <div className="flex items-center gap-1.5 shrink-0">
            <button
              onClick={handleCancel}
              disabled={cancelling}
              className={`h-8 px-3 rounded-[6px] flex items-center gap-1.5 transition-colors cursor-pointer ${
                confirmCancel
                  ? "bg-red-muted border border-red/30 text-red"
                  : "text-text-tertiary hover:text-red hover:bg-red-muted border border-transparent hover:border-red/20"
              } disabled:opacity-50 disabled:cursor-not-allowed`}
              style={{ transitionDuration: "var(--duration-instant)" }}
              title={confirmCancel ? "Confirm cancel" : "Cancel"}
            >
              {cancelling ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <XIcon className="w-3.5 h-3.5" />
              )}
              <span className="text-[11px] font-[560]">
                {confirmCancel ? "Confirm" : "Cancel"}
              </span>
            </button>
            <ChevronRight className="w-4 h-4 text-text-quaternary/40 group-hover:text-text-quaternary transition-colors" />
          </div>
        </div>

        {/* Trigger text */}
        {trigger.trigger_text && (
          <div className="mb-4 border-l-[2px] border-accent/70 pl-4">
            <p className="font-serif text-[15px] italic text-text-secondary leading-[1.45] line-clamp-2">
              “{trigger.trigger_text}”
            </p>
          </div>
        )}

        {/* Progress */}
        <div className="flex items-center gap-1">
          {Array.from({ length: STEP_COUNT }).map((_, i) => (
            <div
              key={i}
              className={`flex-1 h-[3px] rounded-full transition-colors ${
                i <= idx ? accentClass : "bg-[rgba(255,245,235,0.05)]"
              }`}
              style={{ transitionDuration: "var(--duration-fast)" }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
