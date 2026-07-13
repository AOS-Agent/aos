import { useMemo } from "react";
import { Inbox } from "lucide-react";
import { EmptyState } from "@/components/primitives";
import { TriggerCard } from "./TriggerCard";
import type { Trigger, TriggerStatus } from "@/lib/sentinelApi";
import { isInFlight } from "@/lib/sentinelApi";

type HistoryFilter = "all" | "sent" | "pending" | "failed" | "discarded";

interface Props {
  triggers: Trigger[];
  filter: HistoryFilter;
  onFilterChange: (f: HistoryFilter) => void;
  onSelect: (t: Trigger) => void;
  onApprove: (id: string) => Promise<void>;
  onDiscard: (id: string) => Promise<void>;
}

const FILTERS: HistoryFilter[] = ["all", "pending", "sent", "failed", "discarded"];

const FILTER_LABELS: Record<HistoryFilter, string> = {
  all: "All",
  pending: "Pending",
  sent: "Sent",
  failed: "Failed",
  discarded: "Discarded",
};

function matchesFilter(status: TriggerStatus, filter: HistoryFilter): boolean {
  if (filter === "all") return true;
  if (filter === "discarded") return status === "discarded" || status === "cancelled";
  return status === filter;
}

export function SentinelHistory({
  triggers,
  filter,
  onFilterChange,
  onSelect,
  onApprove,
  onDiscard,
}: Props) {
  const historyTriggers = useMemo(
    () => triggers.filter((t) => !isInFlight(t.status)),
    [triggers],
  );

  const counts = useMemo(() => {
    const c: Record<HistoryFilter, number> = {
      all: historyTriggers.length,
      sent: 0,
      pending: 0,
      failed: 0,
      discarded: 0,
    };
    for (const t of historyTriggers) {
      if (t.status === "sent") c.sent += 1;
      else if (t.status === "pending") c.pending += 1;
      else if (t.status === "failed") c.failed += 1;
      else if (t.status === "discarded" || t.status === "cancelled") c.discarded += 1;
    }
    return c;
  }, [historyTriggers]);

  const filtered = useMemo(
    () => historyTriggers.filter((t) => matchesFilter(t.status, filter)),
    [historyTriggers, filter],
  );

  const sorted = useMemo(
    () => [...filtered].sort((a, b) => b.created_at - a.created_at),
    [filtered],
  );

  return (
    <section>
      <div className="flex items-center justify-between gap-4 mb-5 flex-wrap">
        <h3 className="text-[11px] font-[590] uppercase tracking-[0.14em] text-text-quaternary">
          History
        </h3>

        {/* Filter pills */}
        <div className="flex items-center gap-1 bg-bg-secondary border border-border-secondary rounded-[9px] p-1">
          {FILTERS.map((f) => {
            const active = filter === f;
            return (
              <button
                key={f}
                onClick={() => onFilterChange(f)}
                className={`text-[11.5px] font-[560] px-3 h-7 rounded-[6px] flex items-center gap-2 transition-colors cursor-pointer ${
                  active
                    ? "bg-bg-quaternary text-text"
                    : "text-text-tertiary hover:text-text-secondary"
                }`}
                style={{ transitionDuration: "var(--duration-instant)" }}
              >
                <span>{FILTER_LABELS[f]}</span>
                <span
                  className={`font-mono text-[10px] tabular-nums ${
                    active ? "text-text-secondary" : "text-text-quaternary"
                  }`}
                >
                  {counts[f]}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {sorted.length === 0 ? (
        <EmptyState
          icon={<Inbox />}
          title={
            filter === "pending"
              ? "Nothing waiting for approval"
              : filter === "failed"
                ? "No failures"
                : filter === "sent"
                  ? "Nothing sent yet"
                  : filter === "discarded"
                    ? "Nothing discarded"
                    : "No history yet"
          }
          description={
            filter === "all"
              ? "Triggers will appear here as Sentinel processes them."
              : "Triggers matching this filter will appear here."
          }
        />
      ) : (
        <div className="flex flex-col gap-4">
          {sorted.map((t) => (
            <TriggerCard
              key={t.id}
              trigger={t}
              onSelect={() => onSelect(t)}
              onApprove={onApprove}
              onDiscard={onDiscard}
            />
          ))}
        </div>
      )}
    </section>
  );
}
