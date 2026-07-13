import { useState, useEffect, useCallback, useRef, memo } from 'react'
import {
  Check,
  X,
  Pencil,
  ChevronDown,
  ChevronUp,
  ListTodo,
  Gavel,
  FileText,
  MessageSquare,
  AlertCircle,
} from 'lucide-react'
import type { ApprovalItem as ApprovalItemType } from '@/store/companion'

// ---------------------------------------------------------------------------
// ApprovalItem — individual approval in the queue section.
//
// Type icon + label, title + description, key metadata,
// Edit / Approve / Reject, 5-second undo timer with countdown bar.
//
// Approval flow:
//   1. Click Approve -> onStartApproval(id) notifies backend
//   2. Countdown bar animates for 5 seconds
//   3. If Undo clicked: onUndo(id) cancels backend timer, card restores
//   4. If 5s elapse: onApprove(id) removes card locally (backend already executed)
// ---------------------------------------------------------------------------

const TYPE_CONFIG: Record<
  string,
  { icon: typeof ListTodo; label: string; color: string }
> = {
  task: { icon: ListTodo, label: 'Task', color: 'text-blue bg-blue-muted' },
  decision: { icon: Gavel, label: 'Decision', color: 'text-accent bg-accent-muted' },
  vault: { icon: FileText, label: 'Vault', color: 'text-teal bg-teal-muted' },
  reply: { icon: MessageSquare, label: 'Reply', color: 'text-green bg-green-muted' },
  system: { icon: AlertCircle, label: 'System', color: 'text-yellow bg-yellow-muted' },
}

const UNDO_DURATION = 5000

interface ApprovalItemProps {
  item: ApprovalItemType
  onApprove: (id: string) => void
  onStartApproval: (id: string) => void
  onUndo: (id: string) => void
  onDismiss: (id: string) => void
  onEdit: (id: string) => void
}

export const ApprovalItemComponent = memo(function ApprovalItemComponent({
  item,
  onApprove,
  onStartApproval,
  onUndo,
  onDismiss,
  onEdit,
}: ApprovalItemProps) {
  const [expanded, setExpanded] = useState(false)
  const [undoing, setUndoing] = useState(false)
  const [undoProgress, setUndoProgress] = useState(100)
  const undoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const undoRafRef = useRef<number>(0)
  const undoStartRef = useRef(0)
  const ref = useRef<HTMLDivElement>(null)

  const config = TYPE_CONFIG[item.type] ?? TYPE_CONFIG.system

  // Cleanup undo on unmount
  useEffect(() => {
    return () => {
      if (undoTimerRef.current) clearTimeout(undoTimerRef.current)
      cancelAnimationFrame(undoRafRef.current)
    }
  }, [])

  const startUndo = useCallback(() => {
    setUndoing(true)
    setUndoProgress(100)
    undoStartRef.current = Date.now()

    // Notify backend to start its undo timer
    onStartApproval(item.id)

    // Animate progress bar
    const tick = () => {
      const elapsed = Date.now() - undoStartRef.current
      const pct = Math.max(0, 100 - (elapsed / UNDO_DURATION) * 100)
      setUndoProgress(pct)
      if (pct > 0) {
        undoRafRef.current = requestAnimationFrame(tick)
      }
    }
    undoRafRef.current = requestAnimationFrame(tick)

    // Commit after duration — remove card locally (backend already executed)
    undoTimerRef.current = setTimeout(() => {
      setUndoing(false)
      onApprove(item.id)
    }, UNDO_DURATION)
  }, [item.id, onApprove, onStartApproval])

  const cancelUndo = useCallback(() => {
    setUndoing(false)
    setUndoProgress(100)
    if (undoTimerRef.current) clearTimeout(undoTimerRef.current)
    cancelAnimationFrame(undoRafRef.current)

    // Notify backend to cancel its timer and revert to pending
    onUndo(item.id)
  }, [item.id, onUndo])

  const handleApprove = useCallback(() => {
    startUndo()
  }, [startUndo])

  const handleDismiss = useCallback(() => {
    // Animate out
    const el = ref.current
    if (el) {
      el.style.transition = 'transform 200ms var(--ease-out), opacity 200ms var(--ease-out)'
      el.style.transform = 'translateX(-100%)'
      el.style.opacity = '0'
      setTimeout(() => onDismiss(item.id), 200)
    } else {
      onDismiss(item.id)
    }
  }, [item.id, onDismiss])

  // Confidence indicator
  const showConfidence = item.confidence < 0.8
  const confidenceLabel = item.confidence < 0.5 ? 'Low' : 'Med'
  const confidenceColor = item.confidence < 0.5 ? 'text-red' : 'text-yellow'

  return (
    <div
      ref={ref}
      className="
        border-b border-border
        animate-[approval-in_220ms_ease-out]
      "
    >
      {/* Undo state */}
      {undoing ? (
        <div className="px-4 py-3">
          <div className="flex items-center justify-between mb-2">
            <span className="type-caption text-text-tertiary">Approved. Undoing in...</span>
            <button
              onClick={cancelUndo}
              className="type-label text-accent hover:text-accent-hover transition-colors"
            >
              Undo<kbd className="ml-1 text-[9px] opacity-50 hidden md:inline">U</kbd>
            </button>
          </div>
          <div className="w-full h-1 bg-bg-tertiary rounded-full overflow-hidden">
            <div
              className="h-full bg-accent rounded-full"
              style={{
                width: `${undoProgress}%`,
                transition: 'none',
              }}
            />
          </div>
        </div>
      ) : (
        <div className="px-4 py-3">
          {/* Header: type + confidence */}
          <div className="flex items-center justify-between mb-1">
            <span
              className={`
                inline-flex items-center gap-1.5 px-2 h-5 rounded-xs
                text-[10px] font-semibold ${config.color}
              `}
            >
              <config.icon className="w-3 h-3" />
              {config.label}
            </span>
            {showConfidence && (
              <span className={`type-tiny ${confidenceColor}`}>
                {confidenceLabel}
              </span>
            )}
          </div>

          {/* Title */}
          <h4 className="type-label text-text leading-snug mb-0.5">
            {item.title}
          </h4>

          {/* Description */}
          {item.description && (
            <p className="type-caption text-text-tertiary leading-relaxed mb-2">
              {item.description}
            </p>
          )}

          {/* Expandable metadata */}
          {Object.keys(item.metadata).length > 0 && (
            <>
              <button
                onClick={() => setExpanded(!expanded)}
                className="
                  inline-flex items-center gap-1
                  type-tiny text-text-quaternary
                  hover:text-text-tertiary transition-colors
                  mb-2
                "
              >
                {expanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                Details
              </button>
              {expanded && (
                <div className="space-y-1 mb-2 pl-1 animate-[approval-expand_150ms_ease-out]">
                  {Object.entries(item.metadata).map(([key, value]) => (
                    <div key={key} className="flex items-baseline gap-2">
                      <span className="type-tiny text-text-quaternary capitalize min-w-[60px]">
                        {key}:
                      </span>
                      <span className="type-caption text-text-tertiary">
                        {String(value)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {/* Actions */}
          <div className="flex items-center gap-1.5">
            <button
              onClick={handleApprove}
              className="
                flex-1 inline-flex items-center justify-center gap-1.5
                h-7 rounded-xs text-[11px] font-medium
                bg-accent text-white
                hover:bg-accent-hover active:opacity-90
                transition-colors duration-[var(--duration-instant)]
              "
            >
              <Check className="w-3 h-3" />
              Approve<kbd className="ml-1 text-[9px] opacity-50 hidden md:inline">A</kbd>
            </button>
            <button
              onClick={() => onEdit(item.id)}
              className="
                flex-1 inline-flex items-center justify-center gap-1.5
                h-7 rounded-xs text-[11px] font-medium
                bg-transparent text-text-secondary
                border border-border-secondary
                hover:bg-hover hover:text-text active:bg-active
                transition-colors duration-[var(--duration-instant)]
              "
            >
              <Pencil className="w-3 h-3" />
              Edit<kbd className="ml-1 text-[9px] opacity-50 hidden md:inline">E</kbd>
            </button>
            <button
              onClick={handleDismiss}
              title="Dismiss (D)"
              className="
                h-7 px-2 rounded-xs
                inline-flex items-center justify-center
                text-text-quaternary
                hover:bg-hover hover:text-text-tertiary active:bg-active
                transition-colors duration-[var(--duration-instant)]
              "
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}

      <style>{`
        @keyframes approval-in {
          from { opacity: 0; transform: translateY(-4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes approval-expand {
          from { opacity: 0; max-height: 0; }
          to   { opacity: 1; max-height: 200px; }
        }
      `}</style>
    </div>
  )
})
