import { useState } from 'react';
import { Plus, Trash2, ChevronLeft, ChevronRight } from 'lucide-react';
import { useChatStore, type ChatSession } from '@/store/chat';

// ---------------------------------------------------------------------------
// ChatSidebar — docked session list that pushes chat content to the right.
// Glass-styled panel that sits in the flex layout of the Chat page.
// Collapsible to a thin strip.
// ---------------------------------------------------------------------------

function formatRelative(ts: number): string {
  const now = Date.now();
  const diff = now - ts;
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d`;
  return new Date(ts).toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function getPreview(session: ChatSession): string {
  const lastMsg = [...session.messages].reverse().find(m => m.role === 'assistant' || m.role === 'user');
  if (!lastMsg) return 'No messages yet';
  const prefix = lastMsg.role === 'user' ? 'You: ' : '';
  const text = lastMsg.text.replace(/\n/g, ' ').trim();
  return prefix + (text.length > 50 ? text.slice(0, 47) + '...' : text);
}

function SessionItem({ session, isActive, onSelect, onDelete }: {
  session: ChatSession;
  isActive: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`
        group w-full text-left px-3 py-2.5 rounded-[7px] cursor-pointer
        transition-colors
        ${isActive ? 'bg-active' : 'hover:bg-hover'}
      `}
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            {isActive && <span className="w-1.5 h-1.5 rounded-full bg-accent shrink-0" />}
            <span className={`text-[12px] font-[510] truncate block ${isActive ? 'text-text' : 'text-text-secondary'}`}>
              {session.name}
            </span>
          </div>
          <p className="text-[11px] text-text-quaternary truncate mt-0.5 leading-[1.3]">
            {getPreview(session)}
          </p>
        </div>

        <div className="flex items-center gap-1 shrink-0">
          <span className="text-[10px] text-text-quaternary font-[450] tabular-nums">
            {formatRelative(session.messages.length > 0
              ? session.messages[session.messages.length - 1].ts * 1000
              : session.createdAt
            )}
          </span>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            className="w-5 h-5 rounded-[3px] flex items-center justify-center opacity-0 group-hover:opacity-100 text-text-quaternary hover:text-red hover:bg-red-muted transition-all cursor-pointer"
            style={{ transitionDuration: 'var(--duration-instant)' }}
            title="Delete session"
          >
            <Trash2 className="w-3 h-3" />
          </button>
        </div>
      </div>
    </button>
  );
}

export function ChatSidebar() {
  const sessions = useChatStore(s => s.sessions);
  const activeId = useChatStore(s => s.activeId);
  const setActive = useChatStore(s => s.setActive);
  const createSession = useChatStore(s => s.createSession);
  const closeSession = useChatStore(s => s.closeSession);

  const [collapsed, setCollapsed] = useState(false);

  // Collapsed: small glass pill to re-open
  if (collapsed) {
    return (
      <div className="shrink-0 w-14 flex flex-col items-center pt-14 gap-1.5">
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          className="
            w-9 h-9 rounded-full flex items-center justify-center
            bg-bg-secondary/60 backdrop-blur-md
            border border-border/40
            shadow-[0_2px_12px_rgba(0,0,0,0.3)]
            text-text-tertiary hover:text-text-secondary hover:bg-bg-tertiary/70
            transition-all cursor-pointer
          "
          style={{ transitionDuration: 'var(--duration-instant)' }}
          title="Show sessions"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={createSession}
          className="
            w-9 h-9 rounded-full flex items-center justify-center
            bg-bg-secondary/60 backdrop-blur-md
            border border-border/40
            shadow-[0_2px_12px_rgba(0,0,0,0.3)]
            text-text-tertiary hover:text-text-secondary hover:bg-bg-tertiary/70
            transition-all cursor-pointer
          "
          style={{ transitionDuration: 'var(--duration-instant)' }}
          title="New chat"
        >
          <Plus className="w-4 h-4" />
        </button>
      </div>
    );
  }

  // Expanded: glass pill card
  return (
    <div className="shrink-0 w-[256px] p-2 pt-12 pb-3 pl-3">
      <div
        className="
          h-full flex flex-col
          bg-bg-secondary/60 backdrop-blur-xl
          border border-border/40
          rounded-[14px]
          shadow-[0_4px_24px_rgba(0,0,0,0.35)]
          overflow-hidden
        "
      >
        {/* Header */}
        <div className="flex items-center justify-between px-2.5 py-2.5 shrink-0">
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => setCollapsed(true)}
              className="w-6 h-6 rounded-[5px] flex items-center justify-center text-text-quaternary hover:text-text-tertiary hover:bg-hover transition-colors cursor-pointer"
              style={{ transitionDuration: 'var(--duration-instant)' }}
              title="Collapse"
            >
              <ChevronLeft className="w-3.5 h-3.5" />
            </button>
            <span className="text-[10px] font-[590] text-text-tertiary uppercase tracking-[0.06em]">
              Sessions
            </span>
          </div>
          <button
            type="button"
            onClick={createSession}
            className="w-7 h-7 rounded-[7px] flex items-center justify-center text-text-tertiary hover:text-text-secondary hover:bg-hover transition-colors cursor-pointer"
            style={{ transitionDuration: 'var(--duration-instant)' }}
            title="New chat"
          >
            <Plus className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Session list */}
        <div className="flex-1 overflow-y-auto px-1.5 pb-1.5 space-y-px scrollbar-none">
          {sessions.map(session => (
            <SessionItem
              key={session.id}
              session={session}
              isActive={session.id === activeId}
              onSelect={() => setActive(session.id)}
              onDelete={() => closeSession(session.id)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
