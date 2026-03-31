import { useState, useEffect, useCallback, useRef } from 'react';

const API_BASE = 'http://127.0.0.1:4098';

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

export function useChiefStream() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [connected, setConnected] = useState(false);
  const [currentText, setCurrentText] = useState('');
  const [toolStatus, setToolStatus] = useState('');
  const currentTextRef = useRef('');
  const messageIdCounter = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const nextId = () => `msg-${++messageIdCounter.current}`;

  // Load history on mount
  useEffect(() => {
    fetch(`${API_BASE}/history`)
      .then(r => r.json())
      .then((events: ChatEvent[]) => {
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
        setMessages(msgs);
      })
      .catch(() => {});
  }, []);

  // SSE connection with auto-reconnect
  const connect = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
    }

    const es = new EventSource(`${API_BASE}/stream`);
    esRef.current = es;

    es.onopen = () => {
      setConnected(true);
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
    };

    es.onmessage = (ev) => {
      try {
        const event: ChatEvent = JSON.parse(ev.data);

        switch (event.type) {
          case 'user_message':
            setMessages(prev => [...prev, {
              id: nextId(), role: 'user',
              text: event.text || '', source: event.source, ts: event.ts,
            }]);
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
            setMessages(prev => [...prev, {
              id: nextId(), role: 'tool',
              text: event.preview || `Using ${event.name}...`,
              toolName: event.name, toolPreview: event.preview, ts: event.ts,
            }]);
            break;

          case 'tool_result':
            if (event.is_error) {
              setMessages(prev => [...prev, {
                id: nextId(), role: 'tool',
                text: event.preview || 'Tool error',
                isError: true, ts: event.ts,
              }]);
            }
            break;

          case 'result':
            if (currentTextRef.current) {
              setMessages(prev => [...prev, {
                id: nextId(), role: 'assistant',
                text: currentTextRef.current, ts: event.ts,
                durationMs: event.duration_ms, costUsd: event.cost_usd,
              }]);
            }
            setCurrentText('');
            currentTextRef.current = '';
            setToolStatus('');
            setStreaming(false);
            break;

          case 'session_init':
            break;
        }
      } catch {}
    };

    es.onerror = () => {
      setConnected(false);
      es.close();
      reconnectTimer.current = setTimeout(() => connect(), 3000);
    };
  }, []);

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
    messages,
    currentText,
    streaming,
    connected,
    toolStatus,
    sendMessage,
    cancelGeneration,
  };
}
