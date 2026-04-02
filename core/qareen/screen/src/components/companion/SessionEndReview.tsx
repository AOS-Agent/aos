import { useState, useEffect, useCallback } from 'react'
import { Check, X, FileText, Loader2 } from 'lucide-react'
import { Button } from '@/components/primitives'
import type { SessionState, NoteGroup, ApprovalItem } from '@/store/companion'

// ---------------------------------------------------------------------------
// SessionEndReview — shown after a session ends.
//
// Displays: duration, segment count, audio status, editable summary,
// tasks created, vault save toggle. User can Save & Close or Discard.
// ---------------------------------------------------------------------------

interface SessionSummary {
  key_points: string[]
  tasks: string[]
  ideas: string[]
  decisions: string[]
  stats: {
    duration_seconds: number
    segment_count: number
    note_count: number
    approval_count: number
  }
}

interface SessionEndReviewProps {
  session: SessionState
  noteGroups: NoteGroup[]
  approvals: ApprovalItem[]
  onSaveAndClose: (summary: string, saveToVault: boolean) => void
  onDiscard: () => void
}

export function SessionEndReview({
  session,
  noteGroups,
  approvals,
  onSaveAndClose,
  onDiscard,
}: SessionEndReviewProps) {
  const [summary, setSummary] = useState<SessionSummary | null>(null)
  const [summaryText, setSummaryText] = useState('')
  const [saveToVault, setSaveToVault] = useState(true)
  const [loading, setLoading] = useState(true)

  // Fetch session summary from backend
  useEffect(() => {
    let cancelled = false

    async function fetchSummary() {
      try {
        const res = await fetch(`/companion/session/${session.id}/summary`)
        if (res.ok && !cancelled) {
          const data = await res.json()
          setSummary(data)
          // Build editable summary text from key points
          const parts: string[] = []
          if (data.key_points?.length) {
            parts.push('Key Points:')
            data.key_points.forEach((p: string) => parts.push(`- ${p}`))
          }
          if (data.decisions?.length) {
            parts.push('\nDecisions:')
            data.decisions.forEach((d: string) => parts.push(`- ${d}`))
          }
          if (data.ideas?.length) {
            parts.push('\nIdeas:')
            data.ideas.forEach((i: string) => parts.push(`- ${i}`))
          }
          setSummaryText(parts.join('\n') || 'No summary generated.')
        }
      } catch {
        // Build summary from local data if backend unavailable
        const points: string[] = []
        noteGroups.forEach((g) => {
          g.bullets.forEach((b) => {
            if (b.type === 'note' || b.type === 'insight') points.push(b.text)
          })
        })
        const tasks = approvals
          .filter((a) => a.type === 'task' && a.status === 'approved')
          .map((a) => a.title)
        const decisions = approvals
          .filter((a) => a.type === 'decision' && a.status === 'approved')
          .map((a) => a.title)

        const fallbackParts: string[] = []
        if (points.length) {
          fallbackParts.push('Key Points:')
          points.slice(0, 10).forEach((p) => fallbackParts.push(`- ${p}`))
        }
        if (decisions.length) {
          fallbackParts.push('\nDecisions:')
          decisions.forEach((d) => fallbackParts.push(`- ${d}`))
        }
        setSummaryText(fallbackParts.join('\n') || 'Session ended.')
        setSummary({
          key_points: points.slice(0, 10),
          tasks,
          ideas: [],
          decisions,
          stats: {
            duration_seconds: 0,
            segment_count: 0,
            note_count: noteGroups.reduce((n, g) => n + g.bullets.length, 0),
            approval_count: approvals.filter((a) => a.status === 'approved').length,
          },
        })
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchSummary()
    return () => { cancelled = true }
  }, [session.id, noteGroups, approvals])

  const handleSave = useCallback(() => {
    onSaveAndClose(summaryText, saveToVault)
  }, [summaryText, saveToVault, onSaveAndClose])

  // Duration display
  const startTime = new Date(session.startedAt).getTime()
  const elapsed = Math.floor((Date.now() - startTime) / 1000)
  const durationStr = `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`

  const approvedTasks = approvals.filter((a) => a.type === 'task' && a.status === 'approved')
  const approvedDecisions = approvals.filter((a) => a.type === 'decision' && a.status === 'approved')
  const totalNotes = noteGroups.reduce((n, g) => n + g.bullets.length, 0)

  return (
    <div className="flex flex-col items-center justify-center h-full px-6 py-8 animate-[fade-in_300ms_ease-out]">
      <div className="w-full max-w-[600px] space-y-6">
        {/* Header */}
        <div className="space-y-2">
          <h1 className="type-title text-text">Session Ended</h1>
          <p className="type-body text-text-secondary">{session.title}</p>
        </div>

        {/* Stats row */}
        <div className="flex items-center gap-4 flex-wrap">
          <StatChip label="Duration" value={durationStr} />
          <StatChip label="Notes" value={String(totalNotes)} />
          <StatChip label="Tasks" value={String(approvedTasks.length)} />
          <StatChip label="Decisions" value={String(approvedDecisions.length)} />
          {session.type === 'processing' && (
            <StatChip
              label="Processed"
              value={`${session.stats.processed}/${session.stats.total}`}
            />
          )}
        </div>

        {/* Summary */}
        <div className="space-y-2">
          <h2 className="type-overline text-text-quaternary tracking-widest">Summary</h2>
          {loading ? (
            <div className="flex items-center gap-2 py-8 justify-center">
              <Loader2 className="w-4 h-4 text-text-quaternary animate-spin" />
              <span className="type-caption text-text-quaternary">Generating summary...</span>
            </div>
          ) : (
            <textarea
              value={summaryText}
              onChange={(e) => setSummaryText(e.target.value)}
              rows={8}
              className="
                w-full px-3 py-2.5 rounded-sm
                bg-bg-secondary border border-border
                text-[13px] text-text-secondary leading-relaxed
                focus:border-accent/40 focus:outline-none
                resize-y
                transition-colors duration-[var(--duration-fast)]
              "
            />
          )}
        </div>

        {/* Tasks created */}
        {approvedTasks.length > 0 && (
          <div className="space-y-2">
            <h2 className="type-overline text-text-quaternary tracking-widest">
              Tasks Created ({approvedTasks.length})
            </h2>
            <div className="space-y-1">
              {approvedTasks.map((task) => (
                <div
                  key={task.id}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-xs bg-bg-secondary"
                >
                  <Check className="w-3.5 h-3.5 text-green shrink-0" />
                  <span className="type-label text-text-secondary truncate">{task.title}</span>
                  {task.metadata.priority && (
                    <span className="type-tiny text-text-quaternary shrink-0">
                      P{String(task.metadata.priority)}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Decisions */}
        {approvedDecisions.length > 0 && (
          <div className="space-y-2">
            <h2 className="type-overline text-text-quaternary tracking-widest">
              Decisions ({approvedDecisions.length})
            </h2>
            <div className="space-y-1">
              {approvedDecisions.map((d) => (
                <div
                  key={d.id}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-xs bg-bg-secondary"
                >
                  <Check className="w-3.5 h-3.5 text-blue shrink-0" />
                  <span className="type-label text-text-secondary truncate">{d.title}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Vault toggle */}
        <div className="flex items-center gap-3 px-3 py-2.5 rounded-sm bg-bg-secondary border border-border">
          <button
            onClick={() => setSaveToVault(!saveToVault)}
            className={`
              h-5 w-5 rounded-xs shrink-0 border
              inline-flex items-center justify-center
              transition-all duration-[var(--duration-instant)]
              ${saveToVault
                ? 'bg-accent border-accent text-white'
                : 'border-border-secondary text-transparent hover:border-text-quaternary'}
            `}
          >
            <Check className="w-3 h-3" />
          </button>
          <div className="flex items-center gap-2 min-w-0">
            <FileText className="w-4 h-4 text-text-tertiary shrink-0" />
            <div className="min-w-0">
              <p className="type-label text-text-secondary">Save session to vault</p>
              <p className="type-caption text-text-quaternary">
                Exports summary and transcript to ~/vault/knowledge/captures/
              </p>
            </div>
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-3 pt-2">
          <Button
            variant="primary"
            onClick={handleSave}
            disabled={loading}
            icon={<Check className="w-4 h-4" />}
          >
            Save & Close
          </Button>
          <Button
            variant="ghost"
            onClick={onDiscard}
            icon={<X className="w-4 h-4" />}
          >
            Discard Summary
          </Button>
        </div>
      </div>

      <style>{`
        @keyframes fade-in {
          from { opacity: 0; transform: translateY(8px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Stat chip — small inline metric
// ---------------------------------------------------------------------------

function StatChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-1.5 px-2.5 h-7 rounded-xs bg-bg-secondary border border-border">
      <span className="type-tiny text-text-quaternary">{label}</span>
      <span className="type-label text-text-secondary tabular-nums">{value}</span>
    </div>
  )
}
