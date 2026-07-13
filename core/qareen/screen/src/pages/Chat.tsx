import { useState, useRef, useEffect, useCallback } from 'react';
import { Send, Loader2, Square, Copy, Check, ArrowDown, Reply, X, MessageSquare, Plus, Image, FileText } from 'lucide-react';
import { useChatStream, type ChatMessage } from '@/hooks/useChatStream';
import { useChatStore } from '@/store/chat';
import { SessionTabs } from '@/components/chat/SessionTabs';
import { ChatSidebar } from '@/components/chat/ChatSidebar';
import { Markdown } from '@/components/chat/Markdown';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ToolGroup({ msgs, isLast, streaming }: { msgs: ChatMessage[]; isLast: boolean; streaming: boolean }) {
  const [open, setOpen] = useState(false);
  const isRunning = isLast && streaming;
  const count = msgs.length;
  const lastTool = msgs[msgs.length - 1];

  function cleanName(msg: ChatMessage): string {
    if (msg.toolName) return msg.toolName;
    return msg.text
      .replace(/^(Using |Running:\s*)/i, '')
      .replace(/\.{2,}$/, '')
      .trim() || 'action';
  }

  // Unique tool names for the summary
  const toolNames = [...new Set(msgs.map(cleanName))];
  const summary = toolNames.length <= 3
    ? toolNames.join(', ')
    : `${toolNames.slice(0, 2).join(', ')} +${toolNames.length - 2} more`;

  return (
    <div className="my-1.5 text-[11px] text-text-quaternary">
      {/* Summary — clickable to expand */}
      <button
        type="button"
        onClick={() => !isRunning && setOpen(!open)}
        className={`
          inline-flex items-center gap-1.5
          transition-colors
          ${isRunning ? 'cursor-default' : 'cursor-pointer hover:text-text-tertiary'}
        `}
        style={{ transitionDuration: 'var(--duration-instant)' }}
      >
        {isRunning ? (
          <span className="w-1.5 h-1.5 rounded-full bg-text-quaternary animate-pulse" />
        ) : (
          <span className="w-1.5 h-1.5 rounded-full bg-text-quaternary/40" />
        )}
        <span className="font-[480]">
          {isRunning
            ? <>Working · {cleanName(lastTool)}</>
            : <>{count} {count === 1 ? 'action' : 'actions'} · {summary}</>
          }
        </span>
      </button>

      {/* Expanded — tool name + detail */}
      {open && !isRunning && (
        <div className="mt-1.5 ml-0.5 pl-3 border-l border-border/40 space-y-2 py-1">
          {msgs.map(msg => {
            const name = cleanName(msg);
            const detail = msg.text !== name ? msg.text : msg.toolPreview;
            return (
              <div key={msg.id}>
                <span className="font-[520] text-text-tertiary">{name}</span>
                {detail && (
                  <p className="text-text-quaternary leading-[1.4] break-words">{detail}</p>
                )}
                {msg.isError && (
                  <p className="text-text-quaternary/60 leading-[1.4]">failed</p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ActionBar({ text, onReply }: { text: string; onReply?: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <div
      className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity bg-bg-tertiary rounded-[7px] border border-border shadow-sm px-1 py-0.5"
      style={{ transitionDuration: 'var(--duration-fast)' }}
    >
      <button
        onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1200); }}
        className="w-6 h-6 flex items-center justify-center rounded-sm text-text-quaternary hover:text-text-secondary hover:bg-hover transition-colors cursor-pointer"
        title="Copy"
      >
        {copied ? <Check className="w-3 h-3 text-green" /> : <Copy className="w-3 h-3" />}
      </button>
      {onReply && (
        <button
          onClick={onReply}
          className="w-6 h-6 flex items-center justify-center rounded-sm text-text-quaternary hover:text-text-secondary hover:bg-hover transition-colors cursor-pointer"
          title="Reply"
        >
          <Reply className="w-3 h-3" />
        </button>
      )}
    </div>
  );
}

function Message({ msg, onReply }: { msg: ChatMessage; onReply?: (msg: ChatMessage) => void }) {
  if (msg.role === 'system') {
    return (
      <div className="flex justify-center my-4">
        <span className="text-[10px] font-[510] text-text-quaternary bg-bg-tertiary px-3 py-1 rounded-full">
          {msg.text}
        </span>
      </div>
    );
  }

  if (msg.role === 'tool') return null;

  if (msg.role === 'user') {
    return (
      <div className="group flex flex-col items-end mb-2 mt-4">
        <div
          className="
            max-w-[75%] w-fit
            px-3.5 py-2 rounded-[18px]
            bg-bg-secondary/60 backdrop-blur-md
            border border-border/40
            shadow-[0_2px_12px_rgba(0,0,0,0.2)]
            text-[13px] font-[440] leading-[1.5] text-text tracking-[-0.008em]
          "
        >
          {msg.text}
        </div>
        <div className="flex items-center gap-2 mt-1 mr-1 opacity-0 group-hover:opacity-100 transition-opacity" style={{ transitionDuration: 'var(--duration-fast)' }}>
          <ActionBar text={msg.text} onReply={onReply ? () => onReply(msg) : undefined} />
          <span className="text-[10px] text-text-quaternary">{formatTime(msg.ts)}</span>
        </div>
      </div>
    );
  }

  // Assistant message
  return (
    <div className="group mb-2">
      <Markdown content={msg.text} />
      <div className="flex items-center gap-2 mt-1 opacity-0 group-hover:opacity-100 transition-opacity" style={{ transitionDuration: 'var(--duration-fast)' }}>
        <ActionBar text={msg.text} onReply={onReply ? () => onReply(msg) : undefined} />
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

function StreamingIndicator({ text, toolStatus }: { text: string; toolStatus: string }) {
  if (text) {
    return (
      <div className="mb-4 max-w-[90%]">
        <Markdown content={text} />
        <span className="inline-block w-[2px] h-[13px] bg-accent animate-[blink_1s_steps(2)_infinite] ml-0.5 -mb-[1px]" />
      </div>
    );
  }

  return (
    <div className="mb-4">
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
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-6">
      <div className="w-14 h-14 rounded-full bg-accent/8 flex items-center justify-center mb-5">
        <MessageSquare className="w-6 h-6 text-accent/60" />
      </div>
      <p className="text-[16px] font-[550] text-text mb-2 font-serif tracking-[-0.01em]">
        Start a conversation
      </p>
      <p className="text-[13px] text-text-quaternary max-w-[280px] leading-[1.5]">
        Messages here are the same conversation as Telegram. Type below or send from your phone.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function ChatPage() {
  const { currentText, streaming, connected, toolStatus, sendMessage, cancelGeneration } = useChatStream();

  const activeSession = useChatStore(s => {
    return s.sessions.find(sess => sess.id === s.activeId);
  });
  const messages = activeSession?.messages ?? [];

  const [input, setInput] = useState('');
  const [replyTo, setReplyTo] = useState<ChatMessage | null>(null);
  const [attachments, setAttachments] = useState<File[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isAtBottom = useRef(true);
  const [showScrollDown, setShowScrollDown] = useState(false);

  const checkAtBottom = useCallback(() => {
    if (!scrollRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    const atBottom = scrollHeight - scrollTop - clientHeight < 60;
    isAtBottom.current = atBottom;
    setShowScrollDown(!atBottom && messages.length > 0);
  }, [messages.length]);

  // Auto-scroll when new messages arrive
  useEffect(() => {
    if (isAtBottom.current && scrollRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
    }
  }, [messages, currentText]);

  const scrollToBottom = useCallback(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
    setShowScrollDown(false);
  }, []);

  // Focus textarea on mount and tab switch
  const activeId = useChatStore(s => s.activeId);
  useEffect(() => { textareaRef.current?.focus(); }, [activeId]);

  const resizeTextarea = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 150) + 'px';
  }, []);

  const handleSend = () => {
    const text = input.trim();
    if (!text || streaming) return;
    let fullText = text;
    if (replyTo) {
      const quoted = replyTo.text.slice(0, 300);
      fullText = `[Replying to: "${quoted}"]\n\n${text}`;
    }
    setInput('');
    setReplyTo(null);
    setAttachments([]);
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    sendMessage(fullText);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  const handleReply = (msg: ChatMessage) => {
    setReplyTo(msg);
    textareaRef.current?.focus();
  };

  return (
    <div className="flex h-full">
      {/* ── Docked session sidebar ── */}
      <ChatSidebar />

      {/* ── Chat column ── */}
      <div className="flex flex-col flex-1 min-w-0">
      {/* Tab pills removed — sidebar is the session list */}

      {/* ── Connection lost banner ── */}
      {!connected && (
        <div className="mt-12 mx-5 md:mx-6 px-4 py-2 bg-red-muted border border-red/10 rounded-[7px] flex items-center gap-2 shrink-0">
          <Loader2 className="w-3 h-3 text-red animate-spin" />
          <span className="text-[11px] text-red font-[480]">Connection lost. Reconnecting...</span>
        </div>
      )}

      {/* ── Message area ── */}
      <div
        ref={scrollRef}
        onScroll={checkAtBottom}
        className="flex-1 overflow-y-auto px-5 md:px-6 pt-12 pb-4 relative"
      >
        {messages.length === 0 && !streaming ? (
          <EmptyState />
        ) : (
          <>
            {(() => {
              const elements: React.ReactNode[] = [];
              let toolBatch: ChatMessage[] = [];
              let batchIndex = 0;

              const flushTools = (isLast: boolean) => {
                if (toolBatch.length > 0) {
                  elements.push(
                    <ToolGroup
                      key={`tools-${batchIndex++}`}
                      msgs={[...toolBatch]}
                      isLast={isLast}
                      streaming={streaming}
                    />
                  );
                  toolBatch = [];
                }
              };

              for (const msg of messages) {
                if (msg.role === 'tool') {
                  toolBatch.push(msg);
                } else {
                  flushTools(false);
                  elements.push(<Message key={msg.id} msg={msg} onReply={handleReply} />);
                }
              }
              flushTools(true);
              return elements;
            })()}
            {streaming && <StreamingIndicator text={currentText} toolStatus={toolStatus} />}
          </>
        )}
      </div>

      {/* ── Scroll-to-bottom pill ── */}
      {showScrollDown && (
        <div className="absolute bottom-[72px] left-1/2 -translate-x-1/2 z-10">
          <button
            onClick={scrollToBottom}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-bg-secondary border border-border-secondary shadow-lg text-[11px] text-text-tertiary hover:text-text-secondary hover:bg-bg-tertiary transition-colors cursor-pointer"
          >
            <ArrowDown className="w-3 h-3" />New messages
          </button>
        </div>
      )}

      {/* ── Floating input area ── */}
      <div className="shrink-0 px-4 md:px-8 pb-4 pt-2">
        {/* Reply-to banner */}
        {replyTo && (
          <div className="flex items-center gap-2 mb-2 mx-auto max-w-[680px] px-3 py-1.5 bg-bg-tertiary/80 backdrop-blur-md rounded-full border border-border/40 shadow-[0_2px_8px_rgba(0,0,0,0.2)]">
            <Reply className="w-3 h-3 text-accent shrink-0" />
            <span className="text-[11px] text-text-tertiary truncate flex-1">
              {replyTo.text.slice(0, 120)}
            </span>
            <button
              onClick={() => setReplyTo(null)}
              className="text-text-quaternary hover:text-text-secondary cursor-pointer"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        )}

        {/* Attachment preview strip */}
        {attachments.length > 0 && (
          <div className="flex items-center gap-2 mb-2 mx-auto max-w-[680px] px-1 overflow-x-auto scrollbar-none">
            {attachments.map((file, i) => (
              <div key={i} className="flex items-center gap-1.5 h-7 px-2.5 rounded-full bg-bg-tertiary/70 backdrop-blur-md border border-border/40 shrink-0">
                {file.type.startsWith('image/') ? <Image className="w-3 h-3 text-accent" /> : <FileText className="w-3 h-3 text-text-tertiary" />}
                <span className="text-[11px] text-text-secondary font-[450] truncate max-w-[100px]">{file.name}</span>
                <button
                  onClick={() => setAttachments(prev => prev.filter((_, j) => j !== i))}
                  className="w-3.5 h-3.5 rounded-full flex items-center justify-center text-text-quaternary hover:text-text-secondary hover:bg-active cursor-pointer transition-colors"
                >
                  <X className="w-2.5 h-2.5" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Input bar — glass pill */}
        <div className="mx-auto max-w-[680px]">
          <div
            className="
              flex items-end gap-2
              bg-bg-tertiary/80 backdrop-blur-xl
              border border-border-tertiary
              rounded-[20px] px-2 py-1.5
              shadow-[0_4px_24px_rgba(0,0,0,0.35)]
              focus-within:border-accent/30 focus-within:shadow-[0_4px_24px_rgba(0,0,0,0.4),0_0_0_1px_rgba(217,115,13,0.12)]
              transition-all
            "
            style={{ transitionDuration: 'var(--duration-fast)' }}
          >
            {/* Attach button */}
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 text-text-tertiary hover:text-text-secondary hover:bg-hover transition-colors cursor-pointer"
              style={{ transitionDuration: 'var(--duration-instant)' }}
              title="Attach files"
            >
              <Plus className="w-4 h-4" />
            </button>

            {/* Hidden file input */}
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept="image/*,.pdf,.txt,.md,.csv,.json,.py,.js,.ts,.tsx"
              className="hidden"
              onChange={(e) => {
                if (e.target.files) {
                  setAttachments(prev => [...prev, ...Array.from(e.target.files!)]);
                  e.target.value = '';
                }
              }}
            />

            {/* Text input */}
            <textarea
              ref={textareaRef}
              value={input}
              onChange={e => { setInput(e.target.value); resizeTextarea(); }}
              onKeyDown={handleKeyDown}
              placeholder={streaming ? 'Waiting for response...' : 'Send a message...'}
              disabled={streaming}
              rows={1}
              className="flex-1 bg-transparent text-[13px] font-[440] text-text placeholder:text-text-quaternary outline-none disabled:opacity-40 resize-none leading-[1.5] max-h-[150px] py-1.5"
            />

            {/* Send / Stop button */}
            <button
              onClick={streaming ? cancelGeneration : handleSend}
              disabled={!input.trim() && !streaming && attachments.length === 0}
              className={`
                w-8 h-8 rounded-full flex items-center justify-center shrink-0 transition-all
                ${streaming
                  ? 'bg-red/15 text-red hover:bg-red/25 cursor-pointer !opacity-100'
                  : input.trim() || attachments.length > 0
                    ? 'bg-accent text-bg cursor-pointer hover:bg-accent-hover'
                    : 'text-text-quaternary cursor-default opacity-30'
                }
              `}
              style={{ transitionDuration: 'var(--duration-instant)' }}
              title={streaming ? 'Stop generation' : 'Send message'}
            >
              {streaming
                ? <Square className="w-3 h-3 fill-current" />
                : <Send className="w-3.5 h-3.5" />
              }
            </button>
          </div>
        </div>
      </div>
      </div>{/* end chat column */}
    </div>
  );
}
