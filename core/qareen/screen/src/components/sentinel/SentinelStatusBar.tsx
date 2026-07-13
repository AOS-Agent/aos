import { Pause, Play, Loader2 } from "lucide-react";
import type { SentinelStatus } from "@/lib/sentinelApi";
import { formatUptime } from "@/lib/sentinelApi";

interface Props {
  status: SentinelStatus | null;
  isLoading: boolean;
  isPausing: boolean;
  onToggle: () => void;
}

export function SentinelStatusBar({ status, isLoading, isPausing, onToggle }: Props) {
  if (isLoading || !status) {
    return (
      <div className="rounded-[10px] bg-bg-secondary border border-border-secondary px-7 py-6 animate-pulse">
        <div className="h-7 w-44 bg-bg-tertiary rounded mb-4" />
        <div className="h-4 w-72 bg-bg-tertiary rounded" />
      </div>
    );
  }

  const running = status.service.running;
  const paused = status.paused;
  const stateColor = !running ? "red" : paused ? "yellow" : "green";
  const stateLabel = !running ? "Stopped" : paused ? "Paused" : "Watching";

  const dotClass =
    stateColor === "green" ? "bg-green" : stateColor === "yellow" ? "bg-yellow" : "bg-red";
  const textClass =
    stateColor === "green" ? "text-green" : stateColor === "yellow" ? "text-yellow" : "text-red";

  const c = status.counts_today;

  return (
    <div className="rounded-[10px] bg-bg-secondary border border-border-secondary px-7 py-6">
      {/* Top row — state + pause toggle */}
      <div className="flex items-start justify-between gap-6">
        <div className="min-w-0">
          <div className="text-[10px] font-[590] uppercase tracking-[0.14em] text-text-quaternary mb-2">
            Sentinel
          </div>
          <div className="flex items-baseline gap-3">
            <span className="relative inline-flex items-center justify-center w-3 h-3 -mb-px">
              <span className={`w-2.5 h-2.5 rounded-full ${dotClass}`} />
              {running && !paused && (
                <span className={`absolute inset-0 rounded-full ${dotClass} opacity-40 animate-ping`} />
              )}
            </span>
            <h2
              className={`font-serif text-[32px] leading-[1.05] font-[500] tracking-[-0.015em] ${textClass}`}
            >
              {stateLabel}
            </h2>
          </div>
        </div>

        <button
          onClick={onToggle}
          disabled={!running || isPausing}
          className={`shrink-0 h-9 px-4 rounded-[7px] flex items-center gap-2 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed border ${
            paused
              ? "bg-green-muted border-green/30 hover:bg-green/15 text-green"
              : "bg-bg-tertiary border-border-secondary hover:border-border-tertiary hover:bg-bg-quaternary text-text-secondary"
          }`}
          style={{ transitionDuration: "var(--duration-instant)" }}
        >
          {isPausing ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : paused ? (
            <Play className="w-3.5 h-3.5" />
          ) : (
            <Pause className="w-3.5 h-3.5" />
          )}
          <span className="text-[12px] font-[560] tracking-[0.005em]">
            {paused ? "Resume" : "Pause"}
          </span>
        </button>
      </div>

      {/* Counts row */}
      <div className="mt-7 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2 sm:gap-3">
        <CountCell label="Sent" value={c.sent} color="green" />
        <CountCell label="Pending" value={c.pending} color="yellow" />
        <CountCell label="In flight" value={c.researching + c.spawning + c.draft_ready + c.sending} color="blue" />
        <CountCell label="Blocked" value={c.blocked} color="orange" />
        <CountCell label="Failed" value={c.failed} color="red" />
        <CountCell label="Discarded" value={c.cancelled} color="gray" />
      </div>

      {/* Trigger phrases + meta */}
      <div className="mt-6 pt-5 border-t border-border flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2.5 flex-wrap min-w-0">
          <span className="text-[10px] font-[590] uppercase tracking-[0.12em] text-text-quaternary">
            Listening for
          </span>
          {status.trigger_phrases.map((phrase) => (
            <span
              key={phrase}
              className="font-mono text-[11px] text-accent border border-accent/40 rounded-[5px] px-2 py-0.5"
            >
              {phrase}
            </span>
          ))}
        </div>
        <div className="flex items-center gap-3 text-[10px] text-text-quaternary font-mono shrink-0">
          {running && status.service.pid != null && <span>pid {status.service.pid}</span>}
          {running && status.service.pid != null && <span className="opacity-50">·</span>}
          {running && <span>up {formatUptime(status.service.uptime_seconds)}</span>}
          {running && <span className="opacity-50">·</span>}
          <span>cursor {status.watcher.cursor ?? "—"}</span>
        </div>
      </div>
    </div>
  );
}

function CountCell({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: "green" | "yellow" | "blue" | "red" | "orange" | "gray";
}) {
  const dotClass = {
    green: "bg-green",
    yellow: "bg-yellow",
    blue: "bg-blue",
    red: "bg-red",
    orange: "bg-orange",
    gray: "bg-text-quaternary",
  }[color];

  const muted = value === 0;

  return (
    <div className="flex items-center gap-3 min-w-0">
      <span className={`w-1.5 h-1.5 rounded-full ${dotClass} ${muted ? "opacity-30" : ""}`} />
      <div className="flex items-baseline gap-1.5 min-w-0">
        <span
          className={`font-mono text-[20px] leading-none font-[500] tracking-[-0.02em] ${
            muted ? "text-text-quaternary" : "text-text"
          }`}
        >
          {value}
        </span>
        <span
          className={`text-[11px] tracking-[0.01em] ${
            muted ? "text-text-quaternary" : "text-text-tertiary"
          }`}
        >
          {label}
        </span>
      </div>
    </div>
  );
}
