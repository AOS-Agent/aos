import { create } from 'zustand';
import type { ChatMessage } from '@/hooks/useChatStream';

// ---------------------------------------------------------------------------
// Chat session store — frontend-only sessions over the single Chief SSE pipe.
// Sessions are persisted to localStorage. The SSE stream feeds the active
// session; switching tabs changes which message list is displayed.
// ---------------------------------------------------------------------------

export interface ChatSession {
  id: string;
  name: string;
  createdAt: number;
  messages: ChatMessage[];
}

interface ChatStore {
  sessions: ChatSession[];
  activeId: string;

  // Actions
  createSession: () => string;
  closeSession: (id: string) => void;
  setActive: (id: string) => void;
  renameSession: (id: string, name: string) => void;
  pushMessage: (sessionId: string, msg: ChatMessage) => void;
  setMessages: (sessionId: string, msgs: ChatMessage[]) => void;
  getActive: () => ChatSession | undefined;
}

const STORAGE_KEY = 'qareen-chat-sessions';

function generateId(): string {
  return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
}

function makeSession(name?: string): ChatSession {
  return {
    id: generateId(),
    name: name ?? 'New chat',
    createdAt: Date.now(),
    messages: [],
  };
}

function loadFromStorage(): { sessions: ChatSession[]; activeId: string } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const data = JSON.parse(raw);
      if (data.sessions?.length > 0) {
        return {
          sessions: data.sessions,
          activeId: data.activeId ?? data.sessions[0].id,
        };
      }
    }
  } catch { /* corrupted storage, start fresh */ }

  const first = makeSession();
  return { sessions: [first], activeId: first.id };
}

function saveToStorage(sessions: ChatSession[], activeId: string) {
  try {
    // Only persist last 20 sessions, trim messages to last 200 per session
    const trimmed = sessions.slice(-20).map(s => ({
      ...s,
      messages: s.messages.slice(-200),
    }));
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ sessions: trimmed, activeId }));
  } catch { /* storage full, silently fail */ }
}

/** Derive a session name from the first user message */
export function deriveSessionName(messages: ChatMessage[]): string {
  const first = messages.find(m => m.role === 'user');
  if (!first) return 'New chat';
  const text = first.text.trim();
  if (text.length <= 30) return text;
  return text.slice(0, 27) + '...';
}

const initial = loadFromStorage();

export const useChatStore = create<ChatStore>((set, get) => ({
  sessions: initial.sessions,
  activeId: initial.activeId,

  createSession: () => {
    const session = makeSession();
    set(state => {
      const next = { sessions: [...state.sessions, session], activeId: session.id };
      saveToStorage(next.sessions, next.activeId);
      return next;
    });
    return session.id;
  },

  closeSession: (id) => {
    set(state => {
      const remaining = state.sessions.filter(s => s.id !== id);
      // Never close the last session — create a fresh one instead
      if (remaining.length === 0) {
        const fresh = makeSession();
        saveToStorage([fresh], fresh.id);
        return { sessions: [fresh], activeId: fresh.id };
      }
      // If closing the active tab, switch to the nearest
      let nextActive = state.activeId;
      if (state.activeId === id) {
        const closedIdx = state.sessions.findIndex(s => s.id === id);
        const newIdx = Math.min(closedIdx, remaining.length - 1);
        nextActive = remaining[newIdx].id;
      }
      saveToStorage(remaining, nextActive);
      return { sessions: remaining, activeId: nextActive };
    });
  },

  setActive: (id) => {
    set(state => {
      saveToStorage(state.sessions, id);
      return { activeId: id };
    });
  },

  renameSession: (id, name) => {
    set(state => {
      const sessions = state.sessions.map(s => s.id === id ? { ...s, name } : s);
      saveToStorage(sessions, state.activeId);
      return { sessions };
    });
  },

  pushMessage: (sessionId, msg) => {
    set(state => {
      const sessions = state.sessions.map(s =>
        s.id === sessionId ? { ...s, messages: [...s.messages, msg] } : s,
      );
      // Auto-name from first user message
      const session = sessions.find(s => s.id === sessionId);
      if (session && session.name === 'New chat' && msg.role === 'user') {
        session.name = deriveSessionName(session.messages);
      }
      saveToStorage(sessions, state.activeId);
      return { sessions };
    });
  },

  setMessages: (sessionId, msgs) => {
    set(state => {
      const sessions = state.sessions.map(s =>
        s.id === sessionId ? { ...s, messages: msgs } : s,
      );
      saveToStorage(sessions, state.activeId);
      return { sessions };
    });
  },

  getActive: () => {
    const state = get();
    return state.sessions.find(s => s.id === state.activeId);
  },
}));
