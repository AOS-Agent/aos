import { useState, useEffect, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, Clock,
  CheckCircle2, Lightbulb, FileText,
  Loader2,
} from 'lucide-react'
import { AudioPlayer } from '@/components/companion/AudioPlayer'
import { format } from 'date-fns'

// ---------------------------------------------------------------------------
// SessionDetail — review a completed session.
//
// Route: /sessions/:id
// Data: /companion/session/:id
// ---------------------------------------------------------------------------

interface TranscriptBlock {
  speaker: string
  text: string
  timestamp: number
  start_time: string
}

interface SessionData {
  id: string
  title: string
  date: string
  duration_seconds: number
  participants: string[]
  transcript: TranscriptBlock[]
  notes: Record<string, string[]>
  summary: string
  audio_path: string
}

function formatDuration(seconds: number): string {
  if (!seconds || seconds < 0) return '0s'
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}

/** Strip markdown boilerplate from old meeting summaries */
function cleanSummary(raw: string): string {
  if (!raw) return ''
  return raw
    .split('\n')
    .filter(line => {
      const trimmed = line.trim()
      // Drop ALL markdown headers (##, ###)
      if (/^#{1,4}\s/.test(trimmed)) return false
      // Drop metadata lines (Duration: X | Date: Y | Participant: Z)
      if (/^\*?\*?(Duration|Date|Participant|Participants):\*?\*?\s/i.test(trimmed)) return false
      // Drop HRs
      if (/^---+\s*$/.test(trimmed)) return false
      // Drop markdown table syntax (pipes with dashes)
      if (/^\|[-:\s|]+\|$/.test(trimmed)) return false
      // Drop markdown table rows
      if (/^\|.*\|$/.test(trimmed) && trimmed.includes('|')) return false
      return true
    })
    .join('\n')
    .replace(/^\n+/, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

export default function SessionDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [data, setData] = useState<SessionData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    let cancelled = false
    fetch(`/companion/session/${id}`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
      .then(d => { if (!cancelled) { setData(d); setError(null) } })
      .catch(e => { if (!cancelled) setError(String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [id])

  const noteTopics = useMemo(() =>
    Object.entries(data?.notes || {}).filter(([, items]) => items.length > 0),
    [data]
  )

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-4 h-4 text-text-quaternary animate-spin" />
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="max-w-[720px] mx-auto px-6 pt-10">
        <BackLink onClick={() => navigate('/sessions')} />
        <p className="text-[13px] text-text-quaternary mt-6">{error ?? 'Session not found.'}</p>
      </div>
    )
  }

  const dateObj = data.date ? new Date(data.date) : null
  const dateStr = dateObj ? format(dateObj, 'EEE, MMM d') : ''
  const timeStr = dateObj ? format(dateObj, 'h:mm a') : ''
  const hasAudio = !!data.audio_path
  const summary = cleanSummary(data.summary)
  const tasks = data.notes?.Tasks || data.notes?.tasks || []
  const ideas = data.notes?.Ideas || data.notes?.ideas || []
  const keyPoints = noteTopics.filter(([k]) => !['Tasks', 'Ideas', 'tasks', 'ideas'].includes(k))
  const hasNotes = tasks.length > 0 || ideas.length > 0 || keyPoints.length > 0
  const hasTranscript = data.transcript?.length > 0

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[640px] mx-auto px-6 pt-10 pb-16">
        <BackLink onClick={() => navigate('/sessions')} />

        {/* Title */}
        <h1 className="text-[18px] font-[600] text-text mt-6 mb-1.5 leading-tight">
          {data.title}
        </h1>

        {/* Meta */}
        <div className="flex items-center gap-2 text-[11px] text-text-quaternary mb-8">
          <Clock className="w-3 h-3" />
          <span>{dateStr} at {timeStr}</span>
          {data.duration_seconds > 0 && (
            <>
              <span className="text-border">·</span>
              <span className="tabular-nums">{formatDuration(data.duration_seconds)}</span>
            </>
          )}
          {hasTranscript && (
            <>
              <span className="text-border">·</span>
              <span className="tabular-nums">{data.transcript.length} segments</span>
            </>
          )}
        </div>

        {/* Audio player — compact, no label */}
        {hasAudio && (
          <div className="mb-8">
            <AudioPlayer src={`/companion/session/${data.id}/audio`} />
          </div>
        )}

        {/* Summary */}
        {summary && (
          <div className="mb-8">
            <div className="space-y-2">
              {summary.split('\n').filter(l => l.trim()).map((line, i) => {
                const trimmed = line.trim()
                // Bullet points
                if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
                  const text = trimmed.slice(2)
                    .replace(/\*\*(.+?)\*\*/g, '$1')
                    .replace(/\*(.+?)\*/g, '$1')
                  return (
                    <div key={i} className="flex items-start gap-2.5 py-0.5">
                      <div className="w-1 h-1 rounded-full bg-text-quaternary mt-[8px] shrink-0" />
                      <p className="text-[13px] text-text-secondary leading-relaxed">{text}</p>
                    </div>
                  )
                }
                // Regular text
                const text = trimmed
                  .replace(/\*\*(.+?)\*\*/g, '$1')
                  .replace(/\*(.+?)\*/g, '$1')
                return (
                  <p key={i} className="text-[13px] text-text-secondary leading-relaxed">
                    {text}
                  </p>
                )
              })}
            </div>
          </div>
        )}

        {/* Notes sections — inline, no tabs */}
        {hasNotes && (
          <div className="space-y-6 mb-8">
            {/* Separator */}
            <div className="border-t border-border" />

            {tasks.length > 0 && (
              <NoteSection
                icon={<CheckCircle2 className="w-3.5 h-3.5 text-green" />}
                title="Action items"
                items={tasks}
                dotColor="bg-green"
              />
            )}
            {ideas.length > 0 && (
              <NoteSection
                icon={<Lightbulb className="w-3.5 h-3.5 text-yellow" />}
                title="Ideas"
                items={ideas}
                dotColor="bg-yellow"
              />
            )}
            {keyPoints.map(([topic, items]) => (
              <NoteSection
                key={topic}
                icon={<FileText className="w-3.5 h-3.5 text-text-quaternary" />}
                title={topic}
                items={items}
              />
            ))}
          </div>
        )}

        {/* Transcript — collapsible */}
        {hasTranscript && (
          <TranscriptSection transcript={data.transcript} />
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

function BackLink({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1.5 text-[11px] text-text-quaternary hover:text-text-tertiary transition-colors cursor-pointer"
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      <ArrowLeft className="w-3 h-3" />
      Sessions
    </button>
  )
}

function NoteSection({
  icon,
  title,
  items,
  dotColor = 'bg-text-quaternary',
}: {
  icon: React.ReactNode
  title: string
  items: string[]
  dotColor?: string
}) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        {icon}
        <span className="text-[11px] font-[510] text-text-tertiary">{title}</span>
      </div>
      <div className="space-y-1 pl-6">
        {items.map((item, i) => (
          <div key={i} className="flex items-start gap-2 py-0.5">
            <div className={`w-1 h-1 rounded-full ${dotColor} mt-[8px] shrink-0 opacity-50`} />
            <p className="text-[13px] text-text-secondary leading-relaxed">{item}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

function TranscriptSection({ transcript }: { transcript: TranscriptBlock[] }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div>
      <div className="border-t border-border mb-4" />
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-[11px] font-[510] text-text-quaternary hover:text-text-tertiary transition-colors cursor-pointer mb-3"
        style={{ transitionDuration: 'var(--duration-instant)' }}
      >
        <span>{expanded ? '▾' : '▸'}</span>
        <span>Transcript ({transcript.length} segments)</span>
      </button>

      {expanded && (
        <div className="space-y-px animate-[fadeIn_200ms_ease-out]">
          {transcript.map((block, i) => (
            <div
              key={i}
              className="flex gap-3 px-2 py-1.5 rounded-[5px] hover:bg-hover transition-colors"
              style={{ transitionDuration: 'var(--duration-instant)' }}
            >
              <span className="text-[10px] text-text-quaternary font-mono tabular-nums shrink-0 w-[38px] pt-0.5">
                {block.start_time}
              </span>
              <div className="min-w-0 flex-1">
                <span className={`text-[10px] font-[600] ${block.speaker === 'You' ? 'text-accent' : 'text-blue'}`}>
                  {block.speaker}
                </span>
                <p className="text-[12px] text-text-secondary mt-0.5 leading-relaxed">
                  {block.text}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
      `}</style>
    </div>
  )
}
