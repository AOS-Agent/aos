import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { SentinelStatusBar } from "@/components/sentinel/SentinelStatusBar";
import { SentinelQueue } from "@/components/sentinel/SentinelQueue";
import { SentinelHistory } from "@/components/sentinel/SentinelHistory";
import { TriggerDetailSheet } from "@/components/sentinel/TriggerDetailSheet";
import { useSentinelStream } from "@/hooks/useSentinelStream";
import type { SentinelStatus, Trigger } from "@/lib/sentinelApi";
import { sentinelApi } from "@/lib/sentinelApi";

type HistoryFilter = "all" | "sent" | "pending" | "failed" | "discarded";

export default function SentinelPage() {
  const [status, setStatus] = useState<SentinelStatus | null>(null);
  const [triggers, setTriggers] = useState<Trigger[]>([]);
  const [statusLoading, setStatusLoading] = useState(true);
  const [triggersLoading, setTriggersLoading] = useState(true);
  const [error, setError] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [filter, setFilter] = useState<HistoryFilter>("all");
  const [selected, setSelected] = useState<Trigger | null>(null);

  /* ── Load initial data ────────────────────────────────────────────── */

  const load = useCallback(async () => {
    setError(false);
    setStatusLoading(true);
    setTriggersLoading(true);
    try {
      const [s, t] = await Promise.all([
        sentinelApi.status(),
        sentinelApi.triggers(),
      ]);
      setStatus(s);
      setTriggers(t.triggers || []);
    } catch {
      setError(true);
    } finally {
      setStatusLoading(false);
      setTriggersLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  /* ── SSE: live updates ────────────────────────────────────────────── */

  useSentinelStream({
    onEvent: useCallback((event) => {
      if (event.kind === "status") {
        setStatus(event.status);
      } else if (event.kind === "trigger_state") {
        const incoming = event.trigger;
        setTriggers((prev) => {
          const idx = prev.findIndex((t) => t.id === incoming.id);
          if (idx === -1) return [incoming, ...prev];
          const next = [...prev];
          next[idx] = { ...next[idx], ...incoming };
          return next;
        });
      }
    }, []),
  });

  /* ── Actions ──────────────────────────────────────────────────────── */

  const handlePauseToggle = useCallback(async () => {
    if (!status) return;
    setPausing(true);
    try {
      const result = status.paused ? await sentinelApi.resume() : await sentinelApi.pause();
      if (result.ok) {
        setStatus({ ...status, paused: !status.paused });
      }
    } catch {
      // SSE will recover state on next status push
    } finally {
      setPausing(false);
    }
  }, [status]);

  const handleCancel = useCallback(async (id: string) => {
    try {
      await sentinelApi.cancel(id);
      // Optimistic: mark as cancelled
      setTriggers((prev) =>
        prev.map((t) => (t.id === id ? { ...t, status: "cancelled" } : t)),
      );
    } catch {
      /* state will sync via SSE */
    }
  }, []);

  const handleApprove = useCallback(async (id: string) => {
    try {
      await sentinelApi.send(id);
      setTriggers((prev) =>
        prev.map((t) => (t.id === id ? { ...t, status: "sending" } : t)),
      );
    } catch {
      /* state will sync via SSE */
    }
  }, []);

  const handleDiscard = useCallback(async (id: string) => {
    try {
      await sentinelApi.discard(id);
      setTriggers((prev) =>
        prev.map((t) => (t.id === id ? { ...t, status: "discarded" } : t)),
      );
    } catch {
      /* state will sync via SSE */
    }
  }, []);

  /* ── Stable refs for components ───────────────────────────────────── */

  const triggerList = useMemo(() => triggers, [triggers]);

  return (
    <div className="min-h-full">
      <div className="px-6 md:px-12 py-8 md:py-12 max-w-[960px] mx-auto overflow-y-auto h-full">
        {/* Error banner */}
        {error && (
          <div className="flex items-center gap-3 bg-red-muted rounded-[7px] px-5 py-3.5 mb-6 border border-red/20">
            <AlertTriangle className="w-4 h-4 text-red shrink-0" />
            <span className="text-[13px] text-red flex-1">
              Failed to load Sentinel data.
            </span>
            <button
              type="button"
              onClick={load}
              className="text-[11px] font-[510] text-red hover:text-text flex items-center gap-1.5 transition-colors cursor-pointer"
              style={{ transitionDuration: "var(--duration-instant)" }}
            >
              <RefreshCw className="w-3 h-3" />
              Retry
            </button>
          </div>
        )}

        {/* Zone 1: Status bar */}
        <div className="mb-12">
          <SentinelStatusBar
            status={status}
            isLoading={statusLoading}
            isPausing={pausing}
            onToggle={handlePauseToggle}
          />
        </div>

        {/* Zone 2: Live queue */}
        <div className="mb-14">
          <SentinelQueue
            triggers={triggerList}
            onSelect={setSelected}
            onCancel={handleCancel}
          />
        </div>

        {/* Zone 3: History */}
        <div className="mb-14">
          {triggersLoading ? (
            <div className="space-y-4">
              <div className="h-4 w-32 bg-bg-tertiary rounded animate-pulse" />
              <div className="flex flex-col gap-4">
                {[0, 1, 2].map((i) => (
                  <div
                    key={i}
                    className="h-48 rounded-[10px] bg-bg-secondary animate-pulse"
                  />
                ))}
              </div>
            </div>
          ) : (
            <SentinelHistory
              triggers={triggerList}
              filter={filter}
              onFilterChange={setFilter}
              onSelect={setSelected}
              onApprove={handleApprove}
              onDiscard={handleDiscard}
            />
          )}
        </div>
      </div>

      {/* Detail sheet */}
      {selected && (
        <TriggerDetailSheet
          trigger={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
