// ---------------------------------------------------------------------------
// useSentinelStream — SSE subscription for live Sentinel updates.
//
// Subscribes to /api/sentinel/stream. Reconnects with backoff on error.
// Calls onEvent with parsed payloads when 'trigger_state' or 'status' events
// arrive. Cleanup on unmount.
// ---------------------------------------------------------------------------

import { useEffect, useRef } from "react";
import type { Trigger, SentinelStatus } from "@/lib/sentinelApi";

const SSE_URL = "/api/sentinel/stream";

export type SentinelStreamEvent =
  | { kind: "trigger_state"; trigger: Trigger }
  | { kind: "status"; status: SentinelStatus };

interface UseSentinelStreamOpts {
  onEvent: (event: SentinelStreamEvent) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
}

export function useSentinelStream({
  onEvent,
  onConnect,
  onDisconnect,
}: UseSentinelStreamOpts) {
  const retryCount = useRef(0);
  const esRef = useRef<EventSource | null>(null);
  // Keep latest callbacks without retriggering the effect.
  const onEventRef = useRef(onEvent);
  const onConnectRef = useRef(onConnect);
  const onDisconnectRef = useRef(onDisconnect);

  useEffect(() => {
    onEventRef.current = onEvent;
    onConnectRef.current = onConnect;
    onDisconnectRef.current = onDisconnect;
  }, [onEvent, onConnect, onDisconnect]);

  useEffect(() => {
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      if (cancelled) return;
      let es: EventSource;
      try {
        es = new EventSource(SSE_URL);
      } catch {
        return;
      }
      esRef.current = es;

      es.onopen = () => {
        retryCount.current = 0;
        onConnectRef.current?.();
      };

      es.addEventListener("trigger_state", (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data) as Trigger;
          onEventRef.current({ kind: "trigger_state", trigger: data });
        } catch {
          /* swallow */
        }
      });

      es.addEventListener("status", (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data) as SentinelStatus;
          onEventRef.current({ kind: "status", status: data });
        } catch {
          /* swallow */
        }
      });

      es.onerror = () => {
        onDisconnectRef.current?.();
        es.close();
        esRef.current = null;
        if (cancelled) return;
        const delay = Math.min(1000 * Math.pow(2, retryCount.current), 30000);
        retryCount.current += 1;
        retryTimer = setTimeout(connect, delay);
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      esRef.current?.close();
      esRef.current = null;
    };
  }, []);
}
