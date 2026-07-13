import { useRef, useEffect, useState, useCallback } from 'react';
import { Plus, X } from 'lucide-react';
import { useChatStore, type ChatSession } from '@/store/chat';

// ---------------------------------------------------------------------------
// Session tabs — floating glass pills aligned with the sidebar hamburger pill.
// Same vertical position (top-3), same glass treatment, positioned after
// the sidebar pill. Follows DESIGN.md "Glass Pill Pattern".
//
// Double-click a tab name to rename it inline.
// ---------------------------------------------------------------------------

function Tab({ session, isActive, onSelect, onClose, onRename }: {
  session: ChatSession;
  isActive: boolean;
  onSelect: () => void;
  onClose: () => void;
  onRename: (name: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(session.name);
  const inputRef = useRef<HTMLInputElement>(null);

  const startEditing = useCallback(() => {
    setDraft(session.name);
    setEditing(true);
  }, [session.name]);

  const commitRename = useCallback(() => {
    const trimmed = draft.trim();
    if (trimmed && trimmed !== session.name) {
      onRename(trimmed);
    }
    setEditing(false);
  }, [draft, session.name, onRename]);

  const cancelEditing = useCallback(() => {
    setDraft(session.name);
    setEditing(false);
  }, [session.name]);

  // Focus and select the input when entering edit mode
  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  return (
    <button
      type="button"
      data-active={isActive || undefined}
      onClick={onSelect}
      className={`
        group flex items-center gap-1 h-8 px-3 shrink-0
        rounded-full cursor-pointer
        backdrop-blur-md
        border transition-all
        shadow-[0_2px_12px_rgba(0,0,0,0.3)]
        text-[12px] font-[510]
        ${isActive
          ? 'bg-bg-tertiary/80 text-text border-accent/25'
          : 'bg-bg-secondary/60 text-text-tertiary border-border/40 hover:bg-bg-tertiary/70 hover:text-text-secondary'
        }
      `}
      style={{ transitionDuration: 'var(--duration-fast)' }}
    >
      {/* Accent dot for active */}
      {isActive && (
        <span className="w-1.5 h-1.5 rounded-full bg-accent shrink-0" />
      )}

      {/* Tab name — double-click to edit */}
      {editing ? (
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); commitRename(); }
            if (e.key === 'Escape') { e.preventDefault(); cancelEditing(); }
            e.stopPropagation();
          }}
          onClick={(e) => e.stopPropagation()}
          className="bg-transparent outline-none text-[12px] font-[510] text-text w-[100px] border-b border-accent/40 py-0"
        />
      ) : (
        <span
          className="truncate max-w-[120px]"
          onDoubleClick={(e) => { e.stopPropagation(); startEditing(); }}
        >
          {session.name}
        </span>
      )}

      {/* Close button */}
      {!editing && (
        <span
          role="button"
          onClick={(e) => { e.stopPropagation(); onClose(); }}
          className={`
            w-4 h-4 rounded-full flex items-center justify-center shrink-0
            cursor-pointer transition-all
            ${isActive
              ? 'text-text-tertiary hover:text-text hover:bg-active'
              : 'opacity-0 group-hover:opacity-100 text-text-quaternary hover:text-text-tertiary hover:bg-active'
            }
          `}
          style={{ transitionDuration: 'var(--duration-instant)' }}
        >
          <X className="w-2.5 h-2.5" />
        </span>
      )}
    </button>
  );
}

export function SessionTabs() {
  const sessions = useChatStore(s => s.sessions);
  const activeId = useChatStore(s => s.activeId);
  const setActive = useChatStore(s => s.setActive);
  const createSession = useChatStore(s => s.createSession);
  const closeSession = useChatStore(s => s.closeSession);
  const renameSession = useChatStore(s => s.renameSession);

  const scrollRef = useRef<HTMLDivElement>(null);

  // Scroll active tab into view when it changes
  useEffect(() => {
    if (!scrollRef.current) return;
    const activeEl = scrollRef.current.querySelector('[data-active="true"]');
    activeEl?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
  }, [activeId]);

  return (
    <div className="fixed top-3 left-[168px] z-[310] flex items-center gap-1.5">
      {/* Tab pills */}
      <div
        ref={scrollRef}
        className="flex items-center gap-1.5 max-w-[calc(100vw-280px)] overflow-x-auto scrollbar-none"
      >
        {sessions.map(session => (
          <Tab
            key={session.id}
            session={session}
            isActive={session.id === activeId}
            onSelect={() => setActive(session.id)}
            onClose={() => closeSession(session.id)}
            onRename={(name) => renameSession(session.id, name)}
          />
        ))}
      </div>

      {/* New session pill */}
      <button
        type="button"
        onClick={createSession}
        className="
          w-8 h-8 rounded-full flex items-center justify-center shrink-0
          bg-bg-secondary/60 backdrop-blur-md
          border border-border/40
          shadow-[0_2px_12px_rgba(0,0,0,0.3)]
          text-text-tertiary hover:text-text-secondary hover:bg-bg-tertiary/70
          transition-all cursor-pointer
        "
        style={{ transitionDuration: 'var(--duration-instant)' }}
        title="New chat"
      >
        <Plus className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}
