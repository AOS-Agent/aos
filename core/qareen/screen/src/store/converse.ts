import { create } from 'zustand';

// ---------------------------------------------------------------------------
// Converse — ephemeral live-view state, keyed by session id.
//
// Domain data (sessions, messages, actions) lives in react-query, refreshed
// by useConverseStream.ts invalidating on SSE events — that's the source of
// truth. This store holds only what react-query has no place for: connection
// health for the page chrome, and a per-session "last live event" timestamp
// so SessionList can show a brief activity pulse without re-deriving it from
// message timestamps on every render.
// ---------------------------------------------------------------------------

interface ConverseState {
  connected: boolean;
  lastEventAt: Record<string, number>; // session_id -> ms epoch of last SSE event
  setConnected: (connected: boolean) => void;
  touchSession: (sessionId: string) => void;
}

export const useConverseStore = create<ConverseState>((set) => ({
  connected: false,
  lastEventAt: {},

  setConnected: (connected) => set({ connected }),

  touchSession: (sessionId) =>
    set((state) => ({
      lastEventAt: { ...state.lastEventAt, [sessionId]: Date.now() },
    })),
}));
