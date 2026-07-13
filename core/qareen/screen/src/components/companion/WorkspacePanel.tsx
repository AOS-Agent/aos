import { Inbox, FileText } from 'lucide-react'
import { useCompanionStore } from '@/store/companion'
import { NoteGroupComponent } from './NoteGroup'
import { ApprovalItemComponent } from './ApprovalItem'

// ---------------------------------------------------------------------------
// WorkspacePanel — right column (60%).
//
// Notes flow freely at top. Approvals appear below with a subtle separator.
// No solid header bars, no boxes. Content breathes on the dark canvas.
// All text in Inter (sans).
// ---------------------------------------------------------------------------

interface WorkspacePanelProps {
  onApprove: (id: string) => void
  onStartApproval: (id: string) => void
  onUndo: (id: string) => void
  onDismiss: (id: string) => void
  onEdit: (id: string) => void
}

export function WorkspacePanel({ onApprove, onStartApproval, onUndo, onDismiss, onEdit }: WorkspacePanelProps) {
  const noteGroups = useCompanionStore((s) => s.noteGroups)
  const approvals = useCompanionStore((s) => s.approvals)

  // Sort notes: pinned first, then by timestamp desc
  const sortedNotes = [...noteGroups].sort((a, b) => {
    if (a.isPinned && !b.isPinned) return -1
    if (!a.isPinned && b.isPinned) return 1
    return new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  })

  // Show both pending and approved_pending (in undo window) items
  const pendingApprovals = approvals.filter(
    (a) => a.status === 'pending' || a.status === 'approved_pending',
  )

  return (
    <div className="flex flex-col h-full">
      {/* Notes section — flows freely, no header bar */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {sortedNotes.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full py-16 px-8">
            <FileText className="w-7 h-7 text-text-quaternary/20 mb-4" />
            <p
              className="text-[15px] text-text-quaternary/70 text-center leading-relaxed max-w-[280px]"
             
            >
              Start talking — key points, decisions, and action items will appear here.
            </p>
          </div>
        ) : (
          <div className="py-4 px-4 space-y-1">
            {sortedNotes.map((group) => (
              <NoteGroupComponent key={group.id} group={group} />
            ))}
          </div>
        )}
      </div>

      {/* Approvals — appear below notes with a subtle separator, only when present or as empty state */}
      {(pendingApprovals.length > 0 || sortedNotes.length > 0) && (
        <>
          {/* Subtle separator line */}
          <div className="mx-4 border-t border-border/20 shrink-0" />

          {/* Approval count — minimal, only when items exist */}
          {pendingApprovals.length > 0 && (
            <div className="flex items-center gap-2 px-5 pt-3 pb-1 shrink-0">
              <span className="text-[11px] font-[510] text-text-quaternary tracking-wide">
                Approvals
              </span>
              <span className="inline-flex items-center justify-center min-w-[16px] h-[16px] px-1 rounded-full bg-accent text-white text-[9px] font-semibold">
                {pendingApprovals.length}
              </span>
            </div>
          )}
        </>
      )}

      {/* Approvals section */}
      <div className="max-h-[40%] min-h-0 overflow-y-auto">
        {pendingApprovals.length === 0 && sortedNotes.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-6 px-8">
            <Inbox className="w-5 h-5 text-text-quaternary/20 mb-3" />
            <p
              className="text-[13px] text-text-quaternary/60 text-center leading-relaxed"
             
            >
              Actions I propose will appear here for your approval.
            </p>
          </div>
        ) : pendingApprovals.length > 0 ? (
          <div className="px-1 pb-2">
            {pendingApprovals.map((item) => (
              <ApprovalItemComponent
                key={item.id}
                item={item}
                onApprove={onApprove}
                onStartApproval={onStartApproval}
                onUndo={onUndo}
                onDismiss={onDismiss}
                onEdit={onEdit}
              />
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}
