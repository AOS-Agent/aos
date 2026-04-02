import { useState, useEffect, useMemo, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Clock, Search, Play, Eye, Trash2,
  Mic, Brain, ClipboardList, Mail, MessageSquare,
} from 'lucide-react'
import { TabBar, EmptyState, Button } from '@/components/primitives'
import { format, isToday, isYesterday, subDays, isAfter } from 'date-fns'

// ---------------------------------------------------------------------------
// Sessions — history page. Lists all past sessions grouped by date.
//
// Route: /sessions
// Data: fetched from /meetings (existing backend endpoint)
// ---------------------------------------------------------------------------

interface SessionRecord {
  id: string
  title: string
  date: string
  duration_seconds: number
  has_transcript: boolean
  has_summary: boolean
  audio_path: string
  summary_preview?: string
  // Inferred from metadata
  state?: 'active' | 'paused' | 'ended'
  participants?: string[]
}

type FilterTab = 'all' | 'ended' | 'has_audio' | 'has_summary'

const FILTER_TABS = [
  { id: 'all' as const, label: 'All' },
  { id: 'ended' as const, label: 'Completed' },
  { id: 'has_audio' as const, label: 'With audio' },
  { id: 'has_summary' as const, label: 'With summary' },
]

function getSessionIcon(title: string) {
  const lower = title.toLowerCase()
  if (lower.includes('meeting') || lower.includes('call')) return Mic
  if (lower.includes('thinking') || lower.includes('brainstorm')) return Brain
  if (lower.includes('planning') || lower.includes('plan')) return ClipboardList
  if (lower.includes('email') || lower.includes('draft')) return Mail
  return MessageSquare
}

function formatDuration(seconds: number): string {
  if (!seconds || seconds < 0) return '0m'
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}

interface DateGroup {
  label: string
  sessions: SessionRecord[]
}

function groupByDate(sessions: SessionRecord[]): DateGroup[] {
  const groups: Map<string, SessionRecord[]> = new Map()
  const labels: Map<string, string> = new Map()

  for (const s of sessions) {
    if (!s.date) continue
    const d = new Date(s.date)
    let key: string
    let label: string

    if (isToday(d)) {
      key = 'today'
      label = 'Today'
    } else if (isYesterday(d)) {
      key = 'yesterday'
      label = 'Yesterday'
    } else if (isAfter(d, subDays(new Date(), 7))) {
      key = 'this-week'
      label = 'This week'
    } else if (isAfter(d, subDays(new Date(), 30))) {
      key = 'this-month'
      label = 'This month'
    } else {
      key = format(d, 'yyyy-MM')
      label = format(d, 'MMMM yyyy')
    }

    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(s)
    labels.set(key, label)
  }

  // Sort groups in chronological order (today first)
  const order = ['today', 'yesterday', 'this-week', 'this-month']
  const sorted = [...groups.entries()].sort(([a], [b]) => {
    const ai = order.indexOf(a)
    const bi = order.indexOf(b)
    if (ai >= 0 && bi >= 0) return ai - bi
    if (ai >= 0) return -1
    if (bi >= 0) return 1
    return b.localeCompare(a) // Newer months first
  })

  return sorted.map(([key, sessions]) => ({
    label: labels.get(key) ?? key,
    sessions,
  }))
}

export default function Sessions() {
  const navigate = useNavigate()
  const [sessions, setSessions] = useState<SessionRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<FilterTab>('all')
  const [search, setSearch] = useState('')

  // Fetch sessions
  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const res = await fetch('/companion/meetings')
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = await res.json()
        if (!cancelled) {
          setSessions(data)
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
  }, [])

  // Filter and search
  const filtered = useMemo(() => {
    let result = sessions

    // Filter
    switch (filter) {
      case 'ended':
        result = result.filter((s) => s.has_summary || s.has_transcript)
        break
      case 'has_audio':
        result = result.filter((s) => !!s.audio_path)
        break
      case 'has_summary':
        result = result.filter((s) => s.has_summary)
        break
    }

    // Search
    if (search.trim()) {
      const q = search.toLowerCase()
      result = result.filter(
        (s) =>
          s.title.toLowerCase().includes(q) ||
          (s.summary_preview && s.summary_preview.toLowerCase().includes(q)),
      )
    }

    return result
  }, [sessions, filter, search])

  const groups = useMemo(() => groupByDate(filtered), [filtered])

  const handleDelete = useCallback(
    async (id: string, e: React.MouseEvent) => {
      e.stopPropagation()
      try {
        const res = await fetch(`/companion/meetings/${id}`, { method: 'DELETE' })
        if (res.ok) {
          setSessions((prev) => prev.filter((s) => s.id !== id))
        }
      } catch { /* silent */ }
    },
    [],
  )

  // Stats
  const totalDuration = sessions.reduce((sum, s) => sum + (s.duration_seconds || 0), 0)
  const withSummary = sessions.filter(s => s.has_summary).length
  const withAudio = sessions.filter(s => !!s.audio_path).length

  return (
    <div className="h-full overflow-y-auto bg-bg">
      <div className="max-w-[860px] mx-auto px-6 md:px-8 pt-14 pb-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-4">
            <h1 className="text-[22px] font-[680] text-text tracking-[-0.025em] leading-none">Sessions</h1>
            {!loading && <span className="text-[11px] text-text-quaternary font-mono">{sessions.length}</span>}
          </div>
          <Button
            variant="primary"
            size="sm"
            onClick={() => navigate('/')}
            icon={<Play className="w-3.5 h-3.5" />}
          >
            New session
          </Button>
        </div>

        {/* Stats summary */}
        {!loading && sessions.length > 0 && (
          <div className="flex items-center gap-4 mb-6 text-[11px] text-text-quaternary">
            <span>{formatDuration(totalDuration)} total</span>
            <span className="w-px h-3 bg-border-secondary" />
            <span>{withSummary} with summaries</span>
            <span className="w-px h-3 bg-border-secondary" />
            <span>{withAudio} with audio</span>
          </div>
        )}

        {/* Filter bar */}
        <div className="flex items-center gap-3 flex-wrap mb-6">
          <TabBar
            tabs={FILTER_TABS}
            active={filter}
            onChange={(id) => setFilter(id as FilterTab)}
          />
          <div className="relative flex-1 min-w-[180px] max-w-[300px]">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-quaternary" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search sessions..."
              className="w-full h-8 pl-8 pr-3 bg-bg-secondary border border-border rounded-sm text-[12px] text-text placeholder:text-text-quaternary focus:border-accent/40 focus:outline-none transition-colors"
              style={{ transitionDuration: 'var(--duration-fast)' }}
            />
          </div>
        </div>

        {/* Loading */}
        {loading && (
          <div className="space-y-3">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="h-[72px] rounded-[7px] bg-bg-secondary animate-pulse" />
            ))}
          </div>
        )}

        {/* Error */}
        {error && !loading && (
          <div className="px-4 py-3 rounded-[7px] bg-red-muted border border-red/20 text-[13px] text-red" style={{ fontFamily: 'var(--font-serif)' }}>
            Could not load sessions. {error}
          </div>
        )}

        {/* Empty */}
        {!loading && !error && filtered.length === 0 && (
          <EmptyState
            icon={<Clock />}
            title={search ? 'No matching sessions' : 'No sessions yet'}
            description={search ? 'Try adjusting your search or filters.' : 'Start a companion session and it will appear here with its transcript and summary.'}
            action={
              !search ? (
                <Button variant="primary" size="sm" onClick={() => navigate('/')} icon={<Play className="w-3.5 h-3.5" />}>
                  Start session
                </Button>
              ) : undefined
            }
          />
        )}

        {/* Session groups */}
        {!loading && !error && groups.length > 0 && (
          <div className="space-y-8">
            {groups.map((group) => (
              <div key={group.label}>
                <h2 className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary mb-3 px-1">
                  {group.label}
                </h2>
                <div className="space-y-1">
                  {group.sessions.map((s) => (
                    <SessionRow
                      key={s.id}
                      session={s}
                      onView={() => navigate(`/sessions/${s.id}`)}
                      onDelete={(e) => handleDelete(s.id, e)}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SessionRow — single session in the timeline
// ---------------------------------------------------------------------------

interface SessionRowProps {
  session: SessionRecord
  onView: () => void
  onDelete: (e: React.MouseEvent) => void
}

function SessionRow({ session, onView, onDelete }: SessionRowProps) {
  const Icon = getSessionIcon(session.title)
  const dateObj = session.date ? new Date(session.date) : null
  const timeStr = dateObj ? format(dateObj, 'h:mm a') : ''

  return (
    <button
      onClick={onView}
      className="w-full flex items-center gap-3.5 px-3.5 py-3 bg-transparent rounded-[7px] hover:bg-hover transition-all group text-left cursor-pointer"
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      {/* Icon */}
      <div className="w-9 h-9 rounded-[5px] bg-bg-secondary border border-border flex items-center justify-center shrink-0 group-hover:border-border-secondary transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
        <Icon className="w-4 h-4 text-text-tertiary group-hover:text-text-secondary transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }} />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="text-[13px] font-[510] text-text-secondary truncate group-hover:text-text transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
            {session.title}
          </span>
          {session.has_summary && (
            <span className="inline-flex items-center px-1.5 h-[18px] rounded-xs text-[9px] font-[510] bg-green-muted text-green shrink-0">
              Summary
            </span>
          )}
          {session.audio_path && (
            <span className="inline-flex items-center px-1.5 h-[18px] rounded-xs text-[9px] font-[510] bg-blue-muted text-blue shrink-0">
              Audio
            </span>
          )}
        </div>
        {session.summary_preview && (
          <p
            className="text-[12px] text-text-quaternary truncate leading-[1.5]"
            style={{ fontFamily: 'var(--font-serif)' }}
          >
            {session.summary_preview}
          </p>
        )}
      </div>

      {/* Right: duration, time, actions */}
      <div className="flex items-center gap-3.5 shrink-0">
        {session.duration_seconds > 0 && (
          <span className="text-[10px] font-[510] text-text-quaternary tabular-nums">
            {formatDuration(session.duration_seconds)}
          </span>
        )}
        {timeStr && (
          <span className="text-[10px] text-text-quaternary tabular-nums">
            {timeStr}
          </span>
        )}

        {/* Hover actions */}
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity" style={{ transitionDuration: 'var(--duration-instant)' }}>
          <span className="h-6 px-2 rounded-xs bg-bg-tertiary text-text-secondary text-[11px] font-[510] inline-flex items-center gap-1 cursor-pointer hover:bg-bg-quaternary transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
            <Eye className="w-3 h-3" />
            View
          </span>
          <button
            onClick={onDelete}
            className="h-6 w-6 rounded-xs inline-flex items-center justify-center text-text-quaternary hover:text-red hover:bg-red-muted transition-colors cursor-pointer"
            style={{ transitionDuration: 'var(--duration-instant)' }}
            title="Delete session"
          >
            <Trash2 className="w-3 h-3" />
          </button>
        </div>
      </div>
    </button>
  )
}
