import { useRef, useEffect, useCallback, type ReactNode } from 'react'
import { Check, Pencil, X } from 'lucide-react'
import type { Card } from '@/lib/types'

// ---------------------------------------------------------------------------
// CardShell — shared wrapper for all approval card types.
//
// Handles:
//  - Enter animation (translateY + opacity)
//  - Approve / Edit / Dismiss buttons with keyboard labels
//  - Source utterance display
//  - Confidence indicator (only shown for medium/low)
//  - Exit animation on approve (green flash → slide right) or dismiss (slide left)
// ---------------------------------------------------------------------------

interface CardShellProps {
  card: Card
  typeBadge: ReactNode
  children: ReactNode
  onApprove: (id: string) => void
  onDismiss: (id: string) => void
  onEdit: (id: string) => void
  isFocused?: boolean
}

export function CardShell({
  card,
  typeBadge,
  children,
  onApprove,
  onDismiss,
  onEdit,
  isFocused = false,
}: CardShellProps) {
  const ref = useRef<HTMLDivElement>(null)
  const mounted = useRef(false)

  // Enter animation
  useEffect(() => {
    const el = ref.current
    if (!el || mounted.current) return
    mounted.current = true
    el.style.opacity = '0'
    el.style.transform = 'translateY(-8px)'
    requestAnimationFrame(() => {
      el.style.transition = 'opacity 180ms var(--ease-out), transform 180ms var(--ease-out)'
      el.style.opacity = '1'
      el.style.transform = 'translateY(0)'
    })
  }, [])

  // Exit animations
  const animateExit = useCallback(
    (direction: 'approve' | 'dismiss', cb: () => void) => {
      const el = ref.current
      if (!el) { cb(); return }

      if (direction === 'approve') {
        // Green flash, then slide right
        el.style.transition = 'background 150ms'
        el.style.background = 'var(--color-green-muted)'
        setTimeout(() => {
          el.style.transition =
            'transform 220ms var(--ease-out), opacity 220ms var(--ease-out)'
          el.style.transform = 'translateX(100%)'
          el.style.opacity = '0'
          setTimeout(cb, 220)
        }, 150)
      } else {
        el.style.transition =
          'transform 220ms var(--ease-out), opacity 220ms var(--ease-out)'
        el.style.transform = 'translateX(-100%)'
        el.style.opacity = '0'
        setTimeout(cb, 220)
      }
    },
    [],
  )

  const handleApprove = useCallback(() => {
    animateExit('approve', () => onApprove(card.id))
  }, [animateExit, onApprove, card.id])

  const handleDismiss = useCallback(() => {
    animateExit('dismiss', () => onDismiss(card.id))
  }, [animateExit, onDismiss, card.id])

  const handleEdit = useCallback(() => {
    onEdit(card.id)
  }, [onEdit, card.id])

  // Confidence display: only medium (0.5-0.79) and low (<0.5)
  const showConfidence = card.confidence < 0.8
  const confidenceLabel =
    card.confidence < 0.5 ? 'Low confidence' : 'Medium confidence'
  const confidenceColor =
    card.confidence < 0.5 ? 'text-red' : 'text-yellow'

  return (
    <div
      ref={ref}
      className={`
        bg-bg-secondary rounded-sm border overflow-hidden
        ${isFocused ? 'border-accent/40 shadow-medium' : 'border-border'}
      `}
      tabIndex={0}
      role="article"
      aria-label={card.title}
    >
      {/* Header: type badge + confidence */}
      <div className="flex items-center justify-between px-3 pt-3 pb-1">
        {typeBadge}
        {showConfidence && (
          <span className={`type-tiny ${confidenceColor}`}>
            {confidenceLabel}
          </span>
        )}
      </div>

      {/* Title */}
      <div className="px-3 pb-1">
        <h3 className="type-label text-text leading-snug">{card.title}</h3>
      </div>

      {/* Card-type-specific content */}
      <div className="px-3 pb-2">{children}</div>

      {/* Source utterance */}
      {card.source_utterance && (
        <div className="px-3 pb-2">
          <p className="type-caption text-text-quaternary italic leading-relaxed">
            "{card.source_utterance}"
          </p>
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-1.5 px-3 pb-3 pt-1">
        <button
          onClick={handleApprove}
          className="
            flex-1 inline-flex items-center justify-center gap-1.5
            h-8 rounded-xs text-[12px] font-medium
            bg-accent text-white
            hover:bg-accent-hover active:opacity-90
            transition-colors duration-[var(--duration-instant)]
          "
        >
          <Check className="w-3.5 h-3.5" />
          Approve
          <kbd className="ml-1 text-[10px] opacity-60 font-mono">A</kbd>
        </button>
        <button
          onClick={handleEdit}
          className="
            flex-1 inline-flex items-center justify-center gap-1.5
            h-8 rounded-xs text-[12px] font-medium
            bg-transparent text-text-secondary
            border border-border-secondary
            hover:bg-hover hover:text-text active:bg-active
            transition-colors duration-[var(--duration-instant)]
          "
        >
          <Pencil className="w-3.5 h-3.5" />
          Edit
          <kbd className="ml-1 text-[10px] opacity-60 font-mono">E</kbd>
        </button>
        <button
          onClick={handleDismiss}
          className="
            inline-flex items-center justify-center gap-1.5
            h-8 px-3 rounded-xs text-[12px] font-medium
            bg-transparent text-text-tertiary
            hover:bg-hover hover:text-text-secondary active:bg-active
            transition-colors duration-[var(--duration-instant)]
          "
        >
          <X className="w-3.5 h-3.5" />
          <kbd className="text-[10px] opacity-60 font-mono">D</kbd>
        </button>
      </div>
    </div>
  )
}
