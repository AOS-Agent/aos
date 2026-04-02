import { useCallback } from 'react'
import { useCompanionStore } from '@/store/companion'
import type { Card } from '@/lib/types'

const API_BASE = '/companion/cards'

/**
 * useApprovals — manages the approval queue with 5-second undo.
 *
 * Cards arrive via SSE (handled by useCompanion). This hook provides
 * approve (with undo window), dismiss, edit, and batch approve operations.
 *
 * Approval flow:
 *   1. User clicks Approve in ApprovalItem
 *   2. ApprovalItem shows 5-second countdown (handled internally)
 *   3. Hook immediately notifies backend (enters approved_pending)
 *   4. If user clicks Undo within 5s: hook cancels via /undo endpoint
 *   5. If 5s elapse: ApprovalItem calls onApprove, card is removed locally
 *      (backend has already scheduled execution after its own 5s delay)
 *
 * The backend has its own parallel 5-second timer. Both client and
 * server timers must expire for the action to execute.
 */
export function useApprovals() {
  const cards = useCompanionStore((s) => s.cards)
  const removeCard = useCompanionStore((s) => s.removeCard)
  const updateCard = useCompanionStore((s) => s.updateCard)
  const approvals = useCompanionStore((s) => s.approvals)
  const removeApproval = useCompanionStore((s) => s.removeApproval)
  const updateApproval = useCompanionStore((s) => s.updateApproval)

  const pendingCards = cards.filter(
    (c) => c.status === 'pending' || c.status === 'approved_pending',
  )
  const pendingApprovals = approvals.filter(
    (a) => a.status === 'pending' || a.status === 'approved_pending',
  )

  // -- Start approval with undo window --
  // Called by ApprovalItem when user clicks Approve.
  // Notifies backend immediately (starts server-side undo timer).
  // The ApprovalItem handles the client-side countdown UI.
  const startApproval = useCallback(
    async (id: string) => {
      // Notify backend — starts the server-side undo window
      try {
        await fetch(`${API_BASE}/${id}/approve`, { method: 'POST' })
      } catch {
        // Backend unreachable — ApprovalItem still shows undo countdown.
        // The card stays until the client timer fires.
      }
    },
    [],
  )

  // -- Finalize approval (called after undo window expires) --
  // The ApprovalItem calls this after its 5-second countdown completes.
  // By this point the backend has already executed the action.
  const approve = useCallback(
    (id: string) => {
      // Remove from local stores — action already executed on backend
      removeCard(id)
      removeApproval(id)
    },
    [removeCard, removeApproval],
  )

  // -- Undo a pending approval --
  // Called when user clicks Undo during the 5-second window.
  const undo = useCallback(
    async (id: string) => {
      // Restore card to pending in local stores
      updateApproval(id, { status: 'pending', undoTimer: undefined } as Partial<any>)

      // Notify backend to cancel its timer
      try {
        await fetch(`${API_BASE}/${id}/undo`, { method: 'POST' })
      } catch {
        // Best effort — if backend is down, card stays pending locally
      }
    },
    [updateApproval],
  )

  // -- Dismiss a single card --
  const dismiss = useCallback(
    async (id: string) => {
      removeCard(id)
      removeApproval(id)
      try {
        await fetch(`${API_BASE}/${id}/dismiss`, { method: 'POST' })
      } catch {
        // Optimistic removal; SSE reconciles if needed
      }
    },
    [removeCard, removeApproval],
  )

  // -- Edit a card (update local state, send to API) --
  const edit = useCallback(
    async (id: string, changes: Partial<Card>) => {
      updateCard(id, changes)
      updateApproval(id, changes as Record<string, unknown>)
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
    [updateCard, updateApproval],
  )

  // -- Approve all low-risk cards (confidence >= 0.8) --
  const approveAll = useCallback(async () => {
    const lowRisk = pendingCards.filter((c) => c.confidence >= 0.8)
    const ids = lowRisk.map((c) => c.id)

    // Don't remove immediately — batch goes through undo windows.
    // The ApprovalItems will show their individual countdowns.
    try {
      await fetch(`${API_BASE}/approve-batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      })
    } catch {
      // Optimistic; SSE reconciles
    }
  }, [pendingCards])

  return {
    cards: pendingCards,
    approvals: pendingApprovals,
    approve,
    startApproval,
    undo,
    dismiss,
    edit,
    approveAll,
  }
}
