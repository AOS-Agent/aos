import { useCallback } from 'react'
import { useCompanionStore } from '@/store/companion'
import type { Card } from '@/lib/types'

const API_BASE = '/companion/cards'

/**
 * useApprovals — manages the approval queue.
 *
 * Cards arrive via SSE (handled by useCompanion). This hook provides
 * optimistic approve/dismiss/edit operations plus an "approve all" batch
 * for low-risk items.
 */
export function useApprovals() {
  const cards = useCompanionStore((s) => s.cards)
  const removeCard = useCompanionStore((s) => s.removeCard)
  const updateCard = useCompanionStore((s) => s.updateCard)

  const pendingCards = cards.filter((c) => c.status === 'pending')

  // -- Approve a single card --
  const approve = useCallback(
    async (id: string) => {
      // Optimistic: remove immediately
      removeCard(id)
      try {
        await fetch(`${API_BASE}/${id}/approve`, { method: 'POST' })
      } catch {
        // If the API fails, the card is already gone from local state.
        // The next SSE sync will reconcile.
      }
    },
    [removeCard],
  )

  // -- Dismiss a single card --
  const dismiss = useCallback(
    async (id: string) => {
      removeCard(id)
      try {
        await fetch(`${API_BASE}/${id}/dismiss`, { method: 'POST' })
      } catch {
        // Optimistic removal; SSE reconciles if needed
      }
    },
    [removeCard],
  )

  // -- Edit a card (update local state, send to API) --
  const edit = useCallback(
    async (id: string, changes: Partial<Card>) => {
      updateCard(id, changes)
      try {
        await fetch(`${API_BASE}/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(changes),
        })
      } catch {
        // Optimistic; SSE reconciles
      }
    },
    [updateCard],
  )

  // -- Approve all low-risk cards (confidence >= 0.8) --
  const approveAll = useCallback(async () => {
    const lowRisk = pendingCards.filter((c) => c.confidence >= 0.8)
    // Optimistic: remove all immediately
    for (const card of lowRisk) {
      removeCard(card.id)
    }
    try {
      await fetch(`${API_BASE}/approve-batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: lowRisk.map((c) => c.id) }),
      })
    } catch {
      // Optimistic; SSE reconciles
    }
  }, [pendingCards, removeCard])

  return {
    cards: pendingCards,
    approve,
    dismiss,
    edit,
    approveAll,
  }
}
