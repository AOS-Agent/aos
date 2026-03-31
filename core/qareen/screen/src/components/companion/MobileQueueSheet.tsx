import { useState, useCallback } from 'react'
import { ChevronUp, ChevronDown } from 'lucide-react'
import { useApprovals } from '@/hooks/useApprovals'
import { QueueColumn } from './QueueColumn'

// ---------------------------------------------------------------------------
// MobileQueueSheet — bottom sheet wrapper for the queue on small screens.
//
// Shows a handle bar with card count. Tap to expand/collapse.
// When expanded, takes bottom 60% of viewport.
// Uses transform translateY for smooth animation (never height).
// ---------------------------------------------------------------------------

export function MobileQueueSheet() {
  const { cards } = useApprovals()
  const [expanded, setExpanded] = useState(false)

  const toggle = useCallback(() => {
    setExpanded((v) => !v)
  }, [])

  if (cards.length === 0) return null

  return (
    <div
      className="
        fixed inset-x-0 bottom-0
        md:hidden
        z-[var(--z-overlay)]
        transition-transform duration-[var(--duration-normal)] ease-out
      "
      style={{
        transform: expanded ? 'translateY(0)' : 'translateY(calc(100% - 44px))',
      }}
    >
      {/* Handle bar */}
      <button
        onClick={toggle}
        className="
          w-full h-[44px] flex items-center justify-center gap-2
          bg-bg-secondary border-t border-border
          rounded-t-lg
        "
      >
        {expanded ? (
          <ChevronDown className="w-4 h-4 text-text-tertiary" />
        ) : (
          <ChevronUp className="w-4 h-4 text-text-tertiary" />
        )}
        <span className="type-label text-text-secondary">
          {cards.length} pending
        </span>
        <span className="inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full bg-accent text-white text-[10px] font-semibold">
          {cards.length}
        </span>
      </button>

      {/* Queue content */}
      <div className="h-[60vh] bg-bg-panel overflow-hidden">
        <QueueColumn />
      </div>
    </div>
  )
}
