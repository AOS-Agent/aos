'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import { Send, Zap, Wrench, Loader2, Square, ChevronDown, Copy, Check, ArrowDown, RefreshCw, Reply, X, Paperclip, Mic } from 'lucide-react';
import { useChiefStream, ChatMessage } from '@/hooks/useChiefStream';
import { Markdown } from '@/components/chat/Markdown';

// ── Helpers ──────────────────────────────────────────

function formatTime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatCost(usd: number | undefined) {
  if (!usd) return '';
  return `$${usd.toFixed(3)}`;
}

function formatDuration(ms: number | undefined) {
  if (!ms) return '';
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

// ── Tool Call (collapsible) ──────────────────────────

function ToolCall({ msg }: { msg: ChatMessage }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="my-1">
      <button
        onClick={() => setOpen(!open)}
        className={`flex items-center gap-2 px-2.5 py-1 rounded-md text-[11px] transition-colors
          ${msg.isError
            ? 'bg-red-muted/50 text-red border border-red/10'
            : 'bg-bg-tertiary/50 text-text-quaternary hover:bg-bg-tertiary border border-border'
          }`}
        style={{ transitionDuration: 'var(--duration-instant)' }}
      >
        <Wrench className="w-2.5 h-2.5 shrink-0" />
        <span className="font-[480] truncate max-w-[400px]">{msg.text}</span>
        <ChevronDown className={`w-2.5 h-2.5 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && msg.toolPreview && (
        <div className="mt-1 ml-5 px-2.5 py-1.5 rounded-md bg-bg text-[11px] font-mono text-text-tertiary border border-border max-h-[200px] overflow-auto">
          {msg.toolPreview}
        </div>
      )}
    </div>
  );
}

// ── Hover action bar ─────────────────────────────────

function ActionBar({ text, onRetry, onReply }: { text: string; onRetry?: () => void; onReply?: () => void }) {
  const [copied, setCopied] = useState(false);

  return (
    <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity bg-bg-tertiary rounded-md border border-border shadow-sm px-1 py-0.5"
      style={{ transitionDuration: 'var(--duration-fast)' }}>
      <button
        onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1200); }}
        className="w-6 h-6 flex items-center justify-center rounded-sm text-text-quaternary hover:text-text-secondary hover:bg-hover transition-colors"
        title="Copy"
      >
        {copied ? <Check className="w-3 h-3 text-green" /> : <Copy className="w-3 h-3" />}
      </button>
      {onReply && (
        <button
          onClick={onReply}
          className="w-6 h-6 flex items-center justify-center rounded-sm text-text-quaternary hover:text-text-secondary hover:bg-hover transition-colors"
          title="Reply"
        >
          <Reply className="w-3 h-3" />
        </button>
      )}
      {onRetry && (
        <button
          onClick={onRetry}
          className="w-6 h-6 flex items-center justify-center rounded-sm text-text-quaternary hover:text-text-secondary hover:bg-hover transition-colors"
          title="Retry"
        >
          <RefreshCw className="w-3 h-3" />
        </button>
      )}
    </div>
  );
}

// ── Message ──────────────────────────────────────────

function Message({ msg, onRetry, onReply }: { msg: ChatMessage; onRetry?: (text: string) => void; onReply?: (msg: ChatMessage) => void }) {
  if (msg.role === 'system') {
    return (
      <div className="flex justify-center my-4">
        <span className="text-[10px] font-[510] text-text-quaternary bg-bg-tertiary px-3 py-1 rounded-full">
          {msg.text}
        </span>
      </div>
    );
  }

  if (msg.role === 'tool') {
    return <ToolCall msg={msg} />;
  }

  const isUser = msg.role === 'user';

  if (isUser) {
    return (
      <div className="group flex justify-end mb-4 mt-6">
        <div className="max-w-[75%]">
          <div className="flex items-start gap-2 justify-end">
            <ActionBar text={msg.text} onReply={onReply ? () => onReply(msg) : undefined} />
            <div className="px-3.5 py-2.5 rounded-[14px] rounded-br-[4px] bg-accent-muted border-l-2 border-accent">
              <div className="text-[13px] font-[440] leading-[1.5] text-text-secondary tracking-[-0.008em]">
                {msg.text}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2 mt-1 justify-end mr-1">
            <span className="text-[10px] text-text-quaternary">{formatTime(msg.ts)}</span>
            {msg.source && (
              <span className="text-[10px] text-text-quaternary">{msg.source}</span>
            )}
          </div>
        </div>
      </div>
    );
  }

  // Assistant — no bubble, text flows on background
  return (
    <div className="group mb-4 max-w-[90%]">
      {/* Chief label + action bar */}
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-1.5">
          <Zap className="w-3 h-3 text-accent" />
          <span className="text-[10px] font-[590] text-accent uppercase tracking-[0.04em]">Chief</span>
        </div>
        <ActionBar text={msg.text} onReply={onReply ? () => onReply(msg) : undefined} onRetry={onRetry ? () => onRetry(msg.text) : undefined} />
      </div>

      {/* Response body */}
      <div className="pl-[18px]">
        <Markdown content={msg.text} />
      </div>

      {/* Meta */}
      <div className="flex items-center gap-2 mt-1.5 pl-[18px]">
        <span className="text-[10px] text-text-quaternary">{formatTime(msg.ts)}</span>
        {msg.durationMs !== undefined && msg.durationMs > 0 && (
          <span className="text-[10px] text-text-quaternary">{formatDuration(msg.durationMs)}</span>
        )}
        {msg.costUsd !== undefined && msg.costUsd > 0 && (
          <span className="text-[10px] font-mono text-text-quaternary">{formatCost(msg.costUsd)}</span>
        )}
      </div>
    </div>
  );
}

// ── Streaming response ───────────────────────────────

function StreamingMessage({ text }: { text: string }) {
  return (
    <div className="mb-4 max-w-[90%]">
      <div className="flex items-center gap-1.5 mb-1.5">
        <Zap className="w-3 h-3 text-accent" />
        <span className="text-[10px] font-[590] text-accent uppercase tracking-[0.04em]">Chief</span>
      </div>
      <div className="pl-[18px]">
        <Markdown content={text} />
        <span className="inline-block w-[2px] h-[13px] bg-accent animate-[blink_1s_steps(2)_infinite] ml-0.5 -mb-[1px]" />
      </div>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────

export default function ChiefPage() {
  const { messages, currentText, streaming, connected, toolStatus, sendMessage, cancelGeneration } = useChiefStream();
  const [input, setInput] = useState('');
  const [replyTo, setReplyTo] = useState<ChatMessage | null>(null);
  const [attachedImages, setAttachedImages] = useState<File[]>([]);
  const [isRecording, setIsRecording] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const isAtBottom = useRef(true);
  const [showScrollDown, setShowScrollDown] = useState(false);

  // Smart scroll
  const checkAtBottom = useCallback(() => {
    if (!scrollRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    const atBottom = scrollHeight - scrollTop - clientHeight < 60;
    isAtBottom.current = atBottom;
    setShowScrollDown(!atBottom && messages.length > 0);
  }, [messages.length]);

  useEffect(() => {
    if (isAtBottom.current && scrollRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
    }
  }, [messages, currentText]);

  const scrollToBottom = useCallback(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
    setShowScrollDown(false);
  }, []);

  // Focus textarea on mount
  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  // Auto-resize textarea
  const resizeTextarea = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 150) + 'px';
  }, []);

  const handleSend = () => {
    const text = input.trim();
    if (!text || streaming) return;

    // Build message with reply context
    let fullText = text;
    if (replyTo) {
      const quoted = replyTo.text.slice(0, 300);
      fullText = `[Replying to: "${quoted}"]\n\n${text}`;
    }

    setInput('');
    setReplyTo(null);
    setAttachedImages([]);
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
    sendMessage(fullText);
  };

  // Image upload
  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    setAttachedImages(prev => [...prev, ...files.filter(f => f.type.startsWith('image/'))]);
    if (e.target) e.target.value = '';
  };

  // Voice recording
  const toggleRecording = async () => {
    if (isRecording && mediaRecorderRef.current) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      const chunks: Blob[] = [];

      recorder.ondataavailable = (e) => chunks.push(e.data);
      recorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        const blob = new Blob(chunks, { type: 'audio/webm' });

        // Send to Whisper transcriber
        const formData = new FormData();
        formData.append('file', blob, 'voice.webm');
        try {
          const res = await fetch('http://127.0.0.1:7602/transcribe', {
            method: 'POST',
            body: formData,
          });
          const data = await res.json();
          if (data.text) {
            setInput(prev => prev + (prev ? ' ' : '') + data.text);
            textareaRef.current?.focus();
          }
        } catch (err) {
          console.error('Transcription failed:', err);
        }
      };

      mediaRecorderRef.current = recorder;
      recorder.start();
      setIsRecording(true);
    } catch (err) {
      console.error('Mic access denied:', err);
    }
  };

  // Reply to a message
  const handleReply = (msg: ChatMessage) => {
    setReplyTo(msg);
    textareaRef.current?.focus();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-48px)] -my-6 -mx-8">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-border">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-accent/15 flex items-center justify-center">
            <Zap className="w-4 h-4 text-accent" />
          </div>
          <div>
            <h1 className="text-[15px] font-[620] text-text tracking-[-0.01em]">Chief</h1>
            <p className="text-[11px] text-text-quaternary">
              {connected
                ? streaming ? 'Responding...' : 'Connected'
                : 'Disconnected'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-green' : 'bg-red'}`} />
          <span className="text-[10px] text-text-quaternary">
            {connected ? 'Live' : 'Offline'}
          </span>
        </div>
      </div>

      {/* Disconnected banner */}
      {!connected && (
        <div className="px-6 py-2 bg-red-muted border-b border-red/10 flex items-center gap-2">
          <Loader2 className="w-3 h-3 text-red animate-spin" />
          <span className="text-[11px] text-red font-[480]">Connection lost. Reconnecting...</span>
        </div>
      )}

      {/* Messages */}
      <div
        ref={scrollRef}
        onScroll={checkAtBottom}
        className="flex-1 overflow-y-auto px-6 py-4 relative"
      >
        {messages.length === 0 && !streaming && (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <div className="w-12 h-12 rounded-full bg-accent/10 flex items-center justify-center mb-4">
              <Zap className="w-6 h-6 text-accent" />
            </div>
            <p className="text-[15px] font-[550] text-text mb-1">Chief Orchestrator</p>
            <p className="text-[12px] text-text-quaternary max-w-[300px]">
              Same conversation as Telegram. Send a message here or from your phone.
            </p>
          </div>
        )}

        {messages.map(msg => (
          <Message key={msg.id} msg={msg} onReply={handleReply} />
        ))}

        {streaming && currentText && <StreamingMessage text={currentText} />}
        {streaming && !currentText && (
          <div className="mb-4">
            <div className="flex items-center gap-1.5 mb-1.5">
              <Zap className="w-3 h-3 text-accent" />
              <span className="text-[10px] font-[590] text-accent uppercase tracking-[0.04em]">Chief</span>
            </div>
            <div className="pl-[18px]">
              {toolStatus ? (
                <div className="flex items-center gap-2">
                  <Loader2 className="w-3 h-3 text-text-quaternary animate-spin" />
                  <span className="text-[11px] text-text-quaternary">{toolStatus}</span>
                </div>
              ) : (
                <div className="flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-text-quaternary animate-[bounce_1.4s_ease-in-out_infinite]" />
                  <span className="w-1.5 h-1.5 rounded-full bg-text-quaternary animate-[bounce_1.4s_ease-in-out_0.2s_infinite]" />
                  <span className="w-1.5 h-1.5 rounded-full bg-text-quaternary animate-[bounce_1.4s_ease-in-out_0.4s_infinite]" />
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Scroll-to-bottom pill */}
      {showScrollDown && (
        <div className="absolute bottom-[72px] left-1/2 -translate-x-1/2 z-10">
          <button
            onClick={scrollToBottom}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-bg-secondary border border-border-secondary shadow-lg text-[11px] text-text-tertiary hover:text-text-secondary hover:bg-bg-tertiary transition-colors"
            style={{ transitionDuration: 'var(--duration-fast)' }}
          >
            <ArrowDown className="w-3 h-3" />
            New messages
          </button>
        </div>
      )}

      {/* Input area */}
      <div className="px-6 py-3 border-t border-border">
        {/* Reply bar */}
        {replyTo && (
          <div className="flex items-center gap-2 mb-2 px-3 py-1.5 bg-bg-tertiary rounded-lg border border-border">
            <Reply className="w-3 h-3 text-accent shrink-0" />
            <span className="text-[11px] text-text-tertiary truncate flex-1">
              {replyTo.role === 'user' ? 'You' : 'Chief'}: {replyTo.text.slice(0, 100)}
            </span>
            <button onClick={() => setReplyTo(null)} className="text-text-quaternary hover:text-text-secondary">
              <X className="w-3 h-3" />
            </button>
          </div>
        )}

        {/* Image previews */}
        {attachedImages.length > 0 && (
          <div className="flex gap-2 mb-2 px-1">
            {attachedImages.map((file, i) => (
              <div key={i} className="relative w-16 h-16 rounded-lg overflow-hidden border border-border">
                <img src={URL.createObjectURL(file)} alt="" className="w-full h-full object-cover" />
                <button
                  onClick={() => setAttachedImages(prev => prev.filter((_, j) => j !== i))}
                  className="absolute top-0.5 right-0.5 w-4 h-4 rounded-full bg-bg/80 flex items-center justify-center"
                >
                  <X className="w-2.5 h-2.5 text-text-secondary" />
                </button>
              </div>
            ))}
          </div>
        )}

        <div
          className="flex items-end gap-2 bg-bg-secondary border border-border-secondary rounded-[14px] px-3 py-2.5 focus-within:border-accent/30 focus-within:ring-1 focus-within:ring-accent/10 transition-all"
          style={{ transitionDuration: 'var(--duration-fast)' }}
        >
          {/* Attach button */}
          <input ref={fileInputRef} type="file" accept="image/*" multiple onChange={handleFileSelect} className="hidden" />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={streaming}
            className="w-7 h-7 rounded-md flex items-center justify-center shrink-0 text-text-quaternary hover:text-text-secondary hover:bg-hover disabled:opacity-20 transition-colors"
            title="Attach image"
          >
            <Paperclip className="w-3.5 h-3.5" />
          </button>

          <textarea
            ref={textareaRef}
            value={input}
            onChange={e => { setInput(e.target.value); resizeTextarea(); }}
            onKeyDown={handleKeyDown}
            placeholder={streaming ? 'Waiting for response...' : 'Message Chief...'}
            disabled={streaming}
            rows={1}
            className="flex-1 bg-transparent text-[13px] font-[440] text-text placeholder:text-text-quaternary outline-none disabled:opacity-40 resize-none leading-[1.5] max-h-[150px]"
          />

          {/* Voice button */}
          <button
            onClick={toggleRecording}
            disabled={streaming}
            className={`w-7 h-7 rounded-md flex items-center justify-center shrink-0 transition-colors ${
              isRecording
                ? 'text-red bg-red-muted animate-pulse'
                : 'text-text-quaternary hover:text-text-secondary hover:bg-hover disabled:opacity-20'
            }`}
            title={isRecording ? 'Stop recording' : 'Voice input'}
          >
            <Mic className="w-3.5 h-3.5" />
          </button>

          {/* Send / Stop button */}
          <button
            onClick={streaming ? cancelGeneration : handleSend}
            disabled={(!input.trim() && !streaming)}
            className={`w-7 h-7 rounded-md flex items-center justify-center shrink-0 transition-colors ${
              streaming
                ? 'text-red hover:bg-red-muted cursor-pointer !opacity-100'
                : 'text-text-quaternary hover:text-accent hover:bg-hover disabled:opacity-20 disabled:cursor-default'
            }`}
            style={{ transitionDuration: 'var(--duration-instant)' }}
            title={streaming ? 'Stop generation' : 'Send message'}
          >
            {streaming ? <Square className="w-3 h-3 fill-current" /> : <Send className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>
    </div>
  );
}
