import { create } from "zustand";

export interface SystemEvent {
  id: string;
  type: string;
  source: string;
  message: string;
  timestamp: string;
  data?: Record<string, unknown>;
}

export interface RealtimeState {
  /** Recent system events received via SSE. */
  events: SystemEvent[];
  /** Whether the SSE connection is alive. */
  connected: boolean;
  /** Maximum number of events to keep in memory. */
  maxEvents: number;

  addEvent: (event: SystemEvent) => void;
  setConnected: (connected: boolean) => void;
  clearEvents: () => void;
}

const DEFAULT_MAX_EVENTS = 200;

export const useRealtimeStore = create<RealtimeState>((set) => ({
  events: [],
  connected: false,
  maxEvents: DEFAULT_MAX_EVENTS,

  addEvent: (event) =>
    set((state) => {
      const next = [event, ...state.events];
      // Trim to maxEvents to prevent unbounded growth
      if (next.length > state.maxEvents) {
        next.length = state.maxEvents;
      }
      return { events: next };
    }),

  setConnected: (connected) =>
    set({ connected }),

  clearEvents: () =>
    set({ events: [] }),
}));
