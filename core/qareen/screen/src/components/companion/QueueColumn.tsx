import { useEffect, useCallback, useState } from 'react'
import { CheckCheck, Inbox } from 'lucide-react'
import { useApprovals } from '@/hooks/useApprovals'
import { CardRenderer } from './cards/CardRenderer'

// ---------------------------------------------------------------------------
// QueueColumn — right column (320px on desktop, bottom sheet on mobile).
//
// Approval card stack:
//   - Top card: full size, fully interactive, focused by default
//   - Cards 2-3: peeking at 8px offset, 95%/90% scale, reduced opacity
//   - Cards 4+: collapsed as count badge
//   - Keyboard: A (approve), E (edit), D (dismiss) on focused card
//   - Tab to navigate between visible cards
//   - "Approve all" button when 3+ low-risk cards
// ---------------------------------------------------------------------------

export function QueueColumn() {
  const { cards, approve, dismiss, edit, approveAll } = useApprovals()
  const [focusedIndex, setFocusedIndex] = useState(0)

  // Clamp focus index when cards change
  useEffect(() => {
    if (focusedIndex >= cards.length) {
      setFocusedIndex(Math.max(0, cards.length - 1))
    }
  }, [cards.length, focusedIndex])

  // Keyboard handler
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (cards.length === 0) return

      const focused = cards[focusedIndex]
      if (!focused) return

      // Don't capture if user is in an input/textarea
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return

      switch (e.key.toLowerCase()) {
        case 'a':
          e.preventDefault()
          approve(focused.id)
          break
        case 'e':
          e.preventDefault()
          edit(focused.id, {})
          break
        case 'd':
          e.preventDefault()
          dismiss(focused.id)
          break
        case 'tab':
          e.preventDefault()
          setFocusedIndex((i) =>
            e.shiftKey
              ? Math.max(0, i - 1)
              : Math.min(Math.min(cards.length - 1, 2), i + 1),
          )
          break
      }
    },
    [cards, focusedIndex, approve, dismiss, edit],
  )

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])

  // How many low-risk cards exist
  const lowRiskCount = cards.filter((c) => c.confidence >= 0.8).length
  const showApproveAll = lowRiskCount >= 3

  // Visible cards: top 3
  const visibleCards = cards.slice(0, 3)
  const overflowCount = Math.max(0, cards.length - 3)

  // Handle edit: for now, just log — real edit modal would go here
  const handleEdit = useCallback((_id: string) => {
    // TODO: open card edit sheet
  }, [])

  return (
    <div className="h-full flex flex-col bg-bg-panel">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="type-overline text-text-quaternary">Queue</span>
          {cards.length > 0 && (
            <span className="inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full bg-accent text-white text-[10px] font-semibold">
              {cards.length}
            </span>
          )}
        </div>
        {showApproveAll && (
          <button
            onClick={approveAll}
            className="
              inline-flex items-center gap-1.5
              px-2.5 h-6 rounded-xs
              text-[11px] font-medium
              bg-green-muted text-green
              hover:opacity-80
              transition-opacity duration-[var(--duration-instant)]
            "
          >
            <CheckCheck className="w-3 h-3" />
            Approve all ({lowRiskCount})
          </button>
        )}
      </div>

      {/* Card stack */}
      <div className="flex-1 overflow-hidden px-3 pt-3">
        {cards.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full">
            <Inbox className="w-6 h-6 text-text-quaternary opacity-20 mb-2" />
            <p className="type-caption text-text-quaternary text-center">
              No pending decisions
            </p>
          </div>
        ) : (
          <div className="relative">
            {visibleCards.map((card, index) => {
              // Stack offsets: card 0 = top, card 1 = +8px, card 2 = +16px
              const offset = index * 8
              const scale = 1 - index * 0.05 // 1.0, 0.95, 0.90
              const opacity = 1 - index * 0.15 // 1.0, 0.85, 0.70
              const zIndex = visibleCards.length - index

              return (
                <div
                  key={card.id}
                  className="transition-all duration-[var(--duration-fast)]"
                  style={{
                    position: index === 0 ? 'relative' : 'absolute',
                    top: index === 0 ? 0 : 0,
                    left: 0,
                    right: 0,
                    transform: `translateY(${offset}px) scale(${scale})`,
                    transformOrigin: 'top center',
                    opacity,
                    zIndex,
                    pointerEvents: index === 0 ? 'auto' : 'none',
                  }}
                >
                  <CardRenderer
                    card={card}
                    onApprove={approve}
                    onDismiss={dismiss}
                    onEdit={handleEdit}
                    isFocused={index === focusedIndex}
                  />
                </div>
              )
            })}

            {/* Overflow count badge */}
            {overflowCount > 0 && (
              <div
                className="
                  flex items-center justify-center
                  mt-2 py-1.5
                  text-text-quaternary type-tiny
                "
              >
                +{overflowCount} more card{overflowCount !== 1 ? 's' : ''}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Keyboard legend */}
      {cards.length > 0 && (
        <div className="flex items-center justify-center gap-4 px-3 py-2 border-t border-border">
          <KbdHint label="Approve" shortcut="A" />
          <KbdHint label="Edit" shortcut="E" />
          <KbdHint label="Dismiss" shortcut="D" />
          <KbdHint label="Next" shortcut="Tab" />
        </div>
      )}
    </div>
  )
}

function KbdHint({ label, shortcut }: { label: string; shortcut: string }) {
  return (
    <span className="inline-flex items-center gap-1 type-tiny text-text-quaternary">
      <kbd className="inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-[2px] bg-bg-tertiary text-text-tertiary text-[9px] font-mono">
        {shortcut}
      </kbd>
      {label}
    </span>
  )
}
