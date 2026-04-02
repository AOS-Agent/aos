import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, Clock, Users, FileText, MessageSquare,
  Mic, CheckCircle2, Lightbulb, Loader2,
} from 'lucide-react'
import { Button, Badge } from '@/components/primitives'
import { AudioPlayer } from '@/components/companion/AudioPlayer'
import { format } from 'date-fns'

// ---------------------------------------------------------------------------
// SessionDetail — full view of a completed session.
//
// Route: /sessions/:id
// Data: fetched from /meetings/:id (existing backend endpoint)
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

export default function SessionDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [data, setData] = useState<SessionData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'summary' | 'transcript' | 'notes'>('summary')

  useEffect(() => {
    if (!id) return
    let cancelled = false

    async function load() {
      try {
        const res = await fetch(`/meetings/${id}`)
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const d = await res.json()
        if (!cancelled) {
          setData(d)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError(String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [id])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-5 h-5 text-text-quaternary animate-spin" />
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="max-w-[820px] mx-auto space-y-4">
        <button
          onClick={() => navigate('/sessions')}
          className="flex items-center gap-1.5 text-text-tertiary hover:text-text type-label transition-colors"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Back to Sessions
        </button>
        <div className="px-4 py-3 rounded-sm bg-red-muted border border-red/20 text-red type-body">
          {error ?? 'Session not found.'}
        </div>
      </div>
    )
  }

  const dateObj = data.date ? new Date(data.date) : null
  const dateStr = dateObj ? format(dateObj, 'EEEE, MMMM d, yyyy') : ''
  const timeStr = dateObj ? format(dateObj, 'h:mm a') : ''
  const durationMin = Math.floor((data.duration_seconds || 0) / 60)
  const hasAudio = !!data.audio_path

  const noteTopics = Object.entries(data.notes || {}).filter(
    ([, items]) => items.length > 0,
  )
  const tasks = data.notes?.Tasks || []
  const ideas = data.notes?.Ideas || []
  const keyPoints = noteTopics.filter(([k]) => k !== 'Tasks' && k !== 'Ideas')

  return (
    <div className="max-w-[820px] mx-auto space-y-6">
      {/* Back nav */}
      <button
        onClick={() => navigate('/sessions')}
        className="flex items-center gap-1.5 text-text-tertiary hover:text-text type-label transition-colors"
      >
        <ArrowLeft className="w-3.5 h-3.5" />
        Sessions
      </button>

      {/* Header */}
      <div className="space-y-3">
        <h1 className="type-title text-text">{data.title}</h1>
        <div className="flex items-center gap-3 flex-wrap">
          {dateStr && (
            <div className="flex items-center gap-1.5 type-caption text-text-tertiary">
              <Clock className="w-3.5 h-3.5" />
              {dateStr} at {timeStr}
            </div>
          )}
          {durationMin > 0 && (
            <Badge label={`${durationMin}m`} status="neutral" />
          )}
          {data.participants && data.participants.length > 1 && (
            <div className="flex items-center gap-1.5 type-caption text-text-tertiary">
              <Users className="w-3.5 h-3.5" />
              {data.participants.join(', ')}
            </div>
          )}
          {hasAudio && <Badge label="Audio" status="info" />}
          {data.summary && <Badge label="Summary" status="success" />}
          {data.transcript?.length > 0 && (
            <Badge label={`${data.transcript.length} segments`} status="neutral" />
          )}
        </div>
      </div>

      {/* Audio player */}
      {hasAudio && (
        <div className="space-y-2">
          <h2 className="type-overline text-text-quaternary tracking-widest">Audio</h2>
          <AudioPlayer src={`/meetings/${data.id}/audio`} />
        </div>
      )}

      {/* Tab navigation */}
      <div className="flex items-center gap-px border-b border-border">
        <TabButton
          active={activeTab === 'summary'}
          onClick={() => setActiveTab('summary')}
          icon={<FileText className="w-3.5 h-3.5" />}
          label="Summary"
          disabled={!data.summary}
        />
        <TabButton
          active={activeTab === 'transcript'}
          onClick={() => setActiveTab('transcript')}
          icon={<MessageSquare className="w-3.5 h-3.5" />}
          label={`Transcript${data.transcript?.length ? ` (${data.transcript.length})` : ''}`}
          disabled={!data.transcript?.length}
        />
        <TabButton
          active={activeTab === 'notes'}
          onClick={() => setActiveTab('notes')}
          icon={<Lightbulb className="w-3.5 h-3.5" />}
          label={`Notes${noteTopics.length ? ` (${noteTopics.reduce((n, [, v]) => n + v.length, 0)})` : ''}`}
          disabled={noteTopics.length === 0}
        />
      </div>

      {/* Tab content */}
      <div className="min-h-[300px]">
        {activeTab === 'summary' && <SummaryTab summary={data.summary} />}
        {activeTab === 'transcript' && <TranscriptTab transcript={data.transcript} />}
        {activeTab === 'notes' && (
          <NotesTab keyPoints={keyPoints} tasks={tasks} ideas={ideas} />
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Tab button
// ---------------------------------------------------------------------------

function TabButton({
  active,
  onClick,
  icon,
  label,
  disabled,
}: {
  active: boolean
  onClick: () => void
  icon: React.ReactNode
  label: string
  disabled?: boolean
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`
        flex items-center gap-1.5 px-3 py-2.5 -mb-px
        text-[12px] font-[510]
        border-b-2
        transition-all duration-[var(--duration-instant)]
        disabled:opacity-30 disabled:pointer-events-none
        ${active
          ? 'border-accent text-text'
          : 'border-transparent text-text-quaternary hover:text-text-tertiary'}
      `}
    >
      {icon}
      {label}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Summary tab
// ---------------------------------------------------------------------------

function SummaryTab({ summary }: { summary: string }) {
  if (!summary) {
    return (
      <p className="type-body text-text-quaternary py-8 text-center">
        No summary available for this session.
      </p>
    )
  }

  return (
    <div className="meeting-prose py-2">
      <div dangerouslySetInnerHTML={{ __html: markdownToHtml(summary) }} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Transcript tab
// ---------------------------------------------------------------------------

function TranscriptTab({ transcript }: { transcript: TranscriptBlock[] }) {
  if (!transcript?.length) {
    return (
      <p className="type-body text-text-quaternary py-8 text-center">
        No transcript available.
      </p>
    )
  }

  return (
    <div className="space-y-0.5 py-2">
      {transcript.map((block, i) => (
        <div
          key={i}
          className="flex gap-3 px-3 py-2 rounded-xs hover:bg-hover transition-colors group"
        >
          {/* Timestamp */}
          <span className="type-tiny text-text-quaternary font-mono tabular-nums shrink-0 w-[42px] pt-0.5">
            {block.start_time}
          </span>

          {/* Speaker + text */}
          <div className="min-w-0 flex-1">
            <span
              className={`type-tiny font-semibold ${
                block.speaker === 'You' ? 'text-accent' : 'text-blue'
              }`}
            >
              {block.speaker}
            </span>
            <p className="type-body text-text-secondary mt-0.5 leading-relaxed">
              {block.text}
            </p>
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Notes tab
// ---------------------------------------------------------------------------

function NotesTab({
  keyPoints,
  tasks,
  ideas,
}: {
  keyPoints: [string, string[]][]
  tasks: string[]
  ideas: string[]
}) {
  return (
    <div className="space-y-6 py-2">
      {/* Tasks */}
      {tasks.length > 0 && (
        <NoteSection
          title="Action Items"
          icon={<CheckCircle2 className="w-3.5 h-3.5 text-green" />}
          items={tasks}
          color="green"
        />
      )}

      {/* Ideas */}
      {ideas.length > 0 && (
        <NoteSection
          title="Ideas"
          icon={<Lightbulb className="w-3.5 h-3.5 text-yellow" />}
          items={ideas}
          color="yellow"
        />
      )}

      {/* Key points by topic */}
      {keyPoints.map(([topic, items]) => (
        <NoteSection
          key={topic}
          title={topic}
          icon={<FileText className="w-3.5 h-3.5 text-text-tertiary" />}
          items={items}
        />
      ))}
    </div>
  )
}

function NoteSection({
  title,
  icon,
  items,
  color,
}: {
  title: string
  icon: React.ReactNode
  items: string[]
  color?: string
}) {
  const dotColor = color ? `bg-${color}` : 'bg-text-quaternary'

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        {icon}
        <h3 className="type-heading text-text">{title}</h3>
        <span className="type-tiny text-text-quaternary">({items.length})</span>
      </div>
      <div className="space-y-1 pl-1">
        {items.map((item, i) => (
          <div key={i} className="flex items-start gap-2 py-1">
            <div className={`w-1.5 h-1.5 rounded-full ${dotColor} mt-[7px] shrink-0`} />
            <p className="type-body text-text-secondary leading-relaxed">{item}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Minimal markdown → HTML (handles headers, bold, bullets, code)
// ---------------------------------------------------------------------------

function markdownToHtml(md: string): string {
  return md
    .split('\n')
    .map((line) => {
      // Headers
      if (line.startsWith('### ')) return `<h3>${esc(line.slice(4))}</h3>`
      if (line.startsWith('## ')) return `<h2>${esc(line.slice(3))}</h2>`
      if (line.startsWith('# ')) return `<h1>${esc(line.slice(2))}</h1>`

      // Horizontal rule
      if (/^---+\s*$/.test(line)) return '<hr />'

      // Numbered list
      if (/^\d+\.\s/.test(line)) {
        const content = line.replace(/^\d+\.\s/, '')
        return `<li>${inlineFormat(content)}</li>`
      }

      // Bullet list
      if (line.startsWith('- ') || line.startsWith('* ')) {
        return `<li>${inlineFormat(line.slice(2))}</li>`
      }

      // Empty line
      if (!line.trim()) return '<br />'

      // Paragraph
      return `<p>${inlineFormat(line)}</p>`
    })
    .join('\n')
}

function inlineFormat(text: string): string {
  return esc(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`(.+?)`/g, '<code>$1</code>')
}

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
