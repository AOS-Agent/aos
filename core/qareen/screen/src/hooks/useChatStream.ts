import { useState, useEffect, useCallback, useRef } from 'react';
import { useChatStore } from '@/store/chat';

// ---------------------------------------------------------------------------
// Chat SSE hook — listens to the unified Qareen SSE stream for chat.* events.
//
// Phase 1 of chat + companion merge:
//   - All chat requests go through /api/chat/* (Qareen proxies to Bridge)
//   - SSE events come from /companion/stream with `chat.<type>` event names
//     (rebroadcast by qareen.channels.bridge_listener)
//
// The frontend only talks to one backend (Qareen on port 4096). The Bridge
// (port 4098) is invisible to the browser.
// ---------------------------------------------------------------------------

const SSE_URL = '/companion/stream';
const API_BASE = '/api/chat';

export interface ChatEvent {
  ts: number;
  type: string;
  text?: string;
  source?: string;
  tool_id?: string;
  name?: string;
  preview?: string;
  is_error?: boolean;
  session_id?: string;
  model?: string;
  duration_ms?: number;
  cost_usd?: number;
  input_tokens?: number;
  output_tokens?: number;
  num_turns?: number;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'tool' | 'system';
  text: string;
  source?: string;
  ts: number;
  toolName?: string;
  toolPreview?: string;
  isError?: boolean;
  durationMs?: number;
  costUsd?: number;
}

let messageIdCounter = 0;
function nextId() { return `msg-${++messageIdCounter}`; }

export function useChatStream() {
  const [streaming, setStreaming] = useState(false);
  const [connected, setConnected] = useState(false);
  const [currentText, setCurrentText] = useState('');
  const [toolStatus, setToolStatus] = useState('');
  const currentTextRef = useRef('');
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const historyLoaded = useRef(false);

  const activeId = useChatStore(s => s.activeId);
  const pushMessage = useChatStore(s => s.pushMessage);
  const setMessages = useChatStore(s => s.setMessages);
  const activeIdRef = useRef(activeId);
  activeIdRef.current = activeId;

  // Load history into the first session on mount (once)
  useEffect(() => {
    if (historyLoaded.current) return;
    historyLoaded.current = true;

    fetch(`${API_BASE}/history`)
      .then(r => r.json())
      .then((events: ChatEvent[]) => {
        if (!Array.isArray(events)) return;
        const msgs: ChatMessage[] = [];
        let accText = '';

        for (const e of events) {
          if (e.type === 'user_message') {
            msgs.push({
              id: nextId(), role: 'user',
              text: e.text || '', source: e.source, ts: e.ts,
            });
            accText = '';
          } else if (e.type === 'tool_start') {
            msgs.push({
              id: nextId(), role: 'tool',
              text: e.preview || `Using ${e.name}...`,
              toolName: e.name, toolPreview: e.preview, ts: e.ts,
            });
          } else if (e.type === 'text_complete') {
            accText = e.text || '';
          } else if (e.type === 'result' && !e.is_error) {
            msgs.push({
              id: nextId(), role: 'assistant',
              text: accText || e.text || '', ts: e.ts,
              durationMs: e.duration_ms, costUsd: e.cost_usd,
            });
            accText = '';
          }
        }

        // Load history into the active session only if it's empty
        const store = useChatStore.getState();
        const active = store.sessions.find(s => s.id === store.activeId);
        if (active && active.messages.length === 0 && msgs.length > 0) {
          setMessages(store.activeId, msgs);
          // Auto-name from first user message
          const firstUser = msgs.find(m => m.role === 'user');
          if (firstUser && active.name === 'New chat') {
            const text = firstUser.text.trim();
            store.renameSession(store.activeId, text.length <= 30 ? text : text.slice(0, 27) + '...');
          }
        }
      })
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // SSE connection — subscribes to chat.* events on the companion stream
  const connect = useCallback(() => {
    if (esRef.current) esRef.current.close();

    const es = new EventSource(SSE_URL);
    esRef.current = es;

    es.onopen = () => {
      setConnected(true);
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
    };

    // Typed handlers for each chat.* event kind. The server emits these
    // via addEventListener-compatible named events (not onmessage).
    const handleChatEvent = (rawEvent: MessageEvent) => {
      try {
        const event: ChatEvent = JSON.parse(rawEvent.data);
        const sid = activeIdRef.current;

        switch (event.type) {
          case 'user_message':
            pushMessage(sid, {
              id: nextId(), role: 'user',
              text: event.text || '', source: event.source, ts: event.ts,
            });
            setCurrentText('');
            currentTextRef.current = '';
            setToolStatus('');
            setStreaming(true);
            break;

          case 'text_delta':
            currentTextRef.current += (event.text || '');
            setCurrentText(currentTextRef.current);
            setToolStatus('');
            break;

          case 'text_complete':
            currentTextRef.current = event.text || currentTextRef.current;
            setCurrentText(currentTextRef.current);
            break;

          case 'tool_start':
            setToolStatus(event.preview || `Using ${event.name}...`);
            pushMessage(sid, {
              id: nextId(), role: 'tool',
              text: event.preview || `Using ${event.name}...`,
              toolName: event.name, toolPreview: event.preview, ts: event.ts,
            });
            break;

          case 'tool_result':
            if (event.is_error) {
              pushMessage(sid, {
                id: nextId(), role: 'tool',
                text: event.preview || 'Tool error',
                isError: true, ts: event.ts,
              });
            }
            break;

          case 'result':
            if (currentTextRef.current) {
              pushMessage(sid, {
                id: nextId(), role: 'assistant',
                text: currentTextRef.current, ts: event.ts,
                durationMs: event.duration_ms, costUsd: event.cost_usd,
              });
            }
            setCurrentText('');
            currentTextRef.current = '';
            setToolStatus('');
            setStreaming(false);
            break;

          case 'session_init':
            break;
        }
      } catch { /* ignore parse errors */ }
    };

    // All chat.* events share the same handler — the inner `event.type`
    // field distinguishes them.
    const chatEventNames = [
      'chat.user_message',
      'chat.text_delta',
      'chat.text_complete',
      'chat.tool_start',
      'chat.tool_result',
      'chat.result',
      'chat.session_init',
      'chat.rate_limit',
    ];
    for (const name of chatEventNames) {
      es.addEventListener(name, handleChatEvent);
    }

    es.onerror = () => {
      setConnected(false);
      es.close();
      reconnectTimer.current = setTimeout(() => connect(), 3000);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    connect();
    return () => {
      esRef.current?.close();
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    };
  }, [connect]);

  const sendMessage = useCallback(async (text: string) => {
    try {
      await fetch(`${API_BASE}/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, source: 'centcom' }),
      });
    } catch (err) {
      console.error('Failed to send:', err);
    }
  }, []);

  const cancelGeneration = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/cancel`, { method: 'POST' });
    } catch (err) {
      console.error('Failed to cancel:', err);
    }
  }, []);

  return {
    currentText,
    streaming,
    connected,
    toolStatus,
    sendMessage,
    cancelGeneration,
  };
}
