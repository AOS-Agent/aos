import { useState, useCallback, useRef, useEffect } from 'react'
import { Pause, Square, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { useCompanionStore } from '@/store/companion'
import type { SessionState, FocusAnchor } from '@/store/companion'

// ---------------------------------------------------------------------------
// SessionHeader — floating glass pill at top-right during active session.
//
// Shows session title (editable), elapsed timer, pause/stop controls,
// and transcript toggle. Follows glass pill pattern from DESIGN.md.
// ---------------------------------------------------------------------------

interface SessionHeaderProps {
  session: SessionState
  onPause: () => void
  onStop: () => void
}

export function SessionHeader({ session, onPause, onStop }: SessionHeaderProps) {
  const updateSessionTitle = useCompanionStore((s) => s.updateSessionTitle)
  const transcriptCollapsed = useCompanionStore((s) => s.transcriptCollapsed)
  const setTranscriptCollapsed = useCompanionStore((s) => s.setTranscriptCollapsed)
  const focusAnchor = useCompanionStore((s) => s.focusAnchor)

  const [isEditing, setIsEditing] = useState(false)
  const [editValue, setEditValue] = useState(session.title)
  const inputRef = useRef<HTMLInputElement>(null)

  // Elapsed timer
  const [elapsed, setElapsed] = useState('')

  useEffect(() => {
    const update = () => {
      const start = new Date(session.startedAt).getTime()
      const diff = Math.floor((Date.now() - start) / 1000)
      const mins = Math.floor(diff / 60)
      const secs = diff % 60
      setElapsed(`${mins}:${secs.toString().padStart(2, '0')}`)
    }
    update()
    const interval = setInterval(update, 1000)
    return () => clearInterval(interval)
  }, [session.startedAt])

  const startEditing = useCallback(() => {
    setEditValue(session.title)
    setIsEditing(true)
    setTimeout(() => inputRef.current?.select(), 0)
  }, [session.title])

  const commitEdit = useCallback(() => {
    const trimmed = editValue.trim()
    if (trimmed && trimmed !== session.title) {
      updateSessionTitle(trimmed)
    }
    setIsEditing(false)
  }, [editValue, session.title, updateSessionTitle])

  return (
    <div
      className="
        fixed top-3 right-3 z-[320]
        flex items-center gap-2 h-8 px-3
        bg-bg-secondary/60 backdrop-blur-md
        border border-border/40 rounded-full
        shadow-[0_2px_12px_rgba(0,0,0,0.3)]
      "
    >
      {/* Editable title */}
      {isEditing ? (
        <input
          ref={inputRef}
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          onBlur={commitEdit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitEdit()
            if (e.key === 'Escape') setIsEditing(false)
          }}
          className="
            w-28 h-5 px-1
            bg-transparent border-b border-accent/40
            text-[12px] font-[510] text-text
            focus:outline-none
          "
        />
      ) : (
        <button
          onClick={startEditing}
          className="truncate text-[12px] font-[510] text-text-secondary hover:text-text transition-colors max-w-[160px]"
          title="Click to edit session title"
        >
          {session.title}
        </button>
      )}

      {/* Separator */}
      <span className="text-text-quaternary text-[11px]">{'\u00b7'}</span>

      {/* Timer */}
      <span className="text-[11px] text-text-tertiary font-mono tabular-nums">
        {elapsed}
      </span>

      {/* Focus anchor — inline in the pill */}
      {focusAnchor && (
        <FocusAnchorBadge anchor={focusAnchor} />
      )}

      {/* Divider line */}
      <div className="w-px h-3.5 bg-border/40" />

      {/* Transcript toggle */}
      <button
        onClick={() => setTranscriptCollapsed(!transcriptCollapsed)}
        className="
          h-5 w-5 rounded-full
          inline-flex items-center justify-center
          text-text-tertiary hover:text-text
          transition-colors duration-[80ms]
          cursor-pointer
        "
        title={transcriptCollapsed ? 'Show transcript' : 'Hide transcript'}
      >
        {transcriptCollapsed ? (
          <PanelLeftOpen className="w-3 h-3" />
        ) : (
          <PanelLeftClose className="w-3 h-3" />
        )}
      </button>

      {/* Pause */}
      <button
        onClick={onPause}
        className="
          h-5 w-5 rounded-full
          inline-flex items-center justify-center
          text-text-tertiary hover:text-yellow
          transition-colors duration-[80ms]
          cursor-pointer
        "
        title="Pause session"
      >
        <Pause className="w-3 h-3" />
      </button>

      {/* Stop */}
      <button
        onClick={onStop}
        className="
          h-5 w-5 rounded-full
          inline-flex items-center justify-center
          text-text-tertiary hover:text-red
          transition-colors duration-[80ms]
          cursor-pointer
        "
        title="End session"
      >
        <Square className="w-3 h-3" />
      </button>
    </div>
  )
}

function FocusAnchorBadge({ anchor }: { anchor: FocusAnchor }) {
  const pct = anchor.total > 0 ? Math.round((anchor.current / anchor.total) * 100) : 0

  return (
    <div className="flex items-center gap-1.5 shrink-0">
      <span className="text-text-quaternary text-[11px]">{'\u00b7'}</span>
      <span className="text-[10px] font-[510] text-accent">
        {anchor.label} {anchor.current}/{anchor.total}
      </span>
      <div className="w-10 h-1 bg-bg-tertiary rounded-full overflow-hidden">
        <div
          className="h-full bg-accent rounded-full transition-all duration-[var(--duration-normal)]"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}
