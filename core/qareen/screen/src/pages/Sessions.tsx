import { useState, useEffect, useMemo, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Clock, Search, ArrowLeft,
  Mic, Brain, ClipboardList, Mail, MessageSquare,
  Trash2,
} from 'lucide-react'
import { format, isToday, isYesterday, subDays, isAfter } from 'date-fns'

// ---------------------------------------------------------------------------
// Sessions — history of all companion sessions, grouped by date.
//
// Route: /sessions
// Data: /companion/sessions
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
  participants?: string[]
  status?: 'active' | 'paused' | 'ended'
  session_type?: string
  skill?: string
}

type FilterTab = 'all' | 'has_summary' | 'has_audio'

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

function cleanSummaryPreview(raw: string): string {
  if (!raw) return ''
  // Strip markdown headers, bold markers, leading hashes
  return raw
    .replace(/^#+\s*/gm, '')
    .replace(/\*\*/g, '')
    .replace(/^[-*]\s*/gm, '')
    .trim()
    .slice(0, 120)
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

  const order = ['today', 'yesterday', 'this-week', 'this-month']
  const sorted = [...groups.entries()].sort(([a], [b]) => {
    const ai = order.indexOf(a)
    const bi = order.indexOf(b)
    if (ai >= 0 && bi >= 0) return ai - bi
    if (ai >= 0) return -1
    if (bi >= 0) return 1
    return b.localeCompare(a)
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
  const [filter, setFilter] = useState<FilterTab>('all')
  const [search, setSearch] = useState('')

  useEffect(() => {
    let cancelled = false
    fetch('/companion/sessions?limit=100')
      .then(r => { if (!r.ok) throw new Error(); return r.json() })
      .then(data => {
        if (!cancelled) {
          // Filter out empty/noise sessions: no title + no summary + short duration
          const clean = (Array.isArray(data) ? data : []).filter((s: SessionRecord) => {
            const hasTitle = s.title && s.title.trim() && s.title !== 'Untitled Session'
            const hasSummary = s.has_summary
            const hasContent = (s.duration_seconds || 0) > 10
            return hasTitle || hasSummary || hasContent
          })
          setSessions(clean)
        }
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const filtered = useMemo(() => {
    let result = sessions
    if (filter === 'has_summary') result = result.filter(s => s.has_summary)
    if (filter === 'has_audio') result = result.filter(s => !!s.audio_path)
    if (search.trim()) {
      const q = search.toLowerCase()
      result = result.filter(s =>
        s.title?.toLowerCase().includes(q) ||
        s.summary_preview?.toLowerCase().includes(q)
      )
    }
    return result
  }, [sessions, filter, search])

  const groups = useMemo(() => groupByDate(filtered), [filtered])

  const handleDelete = useCallback(async (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      const res = await fetch(`/companion/session/${id}`, { method: 'DELETE' })
      if (res.ok) setSessions(prev => prev.filter(s => s.id !== id))
    } catch { /* silent */ }
  }, [])

  // Stats
  const totalDuration = sessions.reduce((sum, s) => sum + (s.duration_seconds || 0), 0)
  const withSummary = sessions.filter(s => s.has_summary).length

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[720px] mx-auto px-6 pt-10 pb-16">
        {/* Back + meta */}
        <button
          onClick={() => navigate('/')}
          className="flex items-center gap-1.5 text-[11px] text-text-quaternary hover:text-text-tertiary transition-colors mb-6 cursor-pointer"
          style={{ transitionDuration: 'var(--duration-instant)' }}
        >
          <ArrowLeft className="w-3 h-3" />
          Home
        </button>

        {/* Stats line */}
        {!loading && sessions.length > 0 && (
          <div className="flex items-center gap-3 mb-6 text-[11px] text-text-quaternary">
            <span className="tabular-nums">{sessions.length} sessions</span>
            <span className="w-px h-3 bg-border" />
            <span className="tabular-nums">{formatDuration(totalDuration)} total</span>
            <span className="w-px h-3 bg-border" />
            <span className="tabular-nums">{withSummary} with summaries</span>
          </div>
        )}

        {/* Filter + search */}
        <div className="flex items-center gap-2 mb-8">
          {(['all', 'has_summary', 'has_audio'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setFilter(tab)}
              className={`
                h-7 px-3 rounded-full text-[11px] font-[510] cursor-pointer
                transition-all
                ${filter === tab
                  ? 'bg-bg-tertiary text-text-secondary border border-border-secondary'
                  : 'text-text-quaternary hover:text-text-tertiary hover:bg-hover border border-transparent'}
              `}
              style={{ transitionDuration: 'var(--duration-instant)' }}
            >
              {tab === 'all' ? 'All' : tab === 'has_summary' ? 'With summary' : 'With audio'}
            </button>
          ))}

          <div className="flex-1" />

          <div className="relative max-w-[220px] w-full">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3 h-3 text-text-quaternary" />
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search..."
              className="
                w-full h-7 pl-7 pr-3
                bg-transparent border border-border rounded-full
                text-[11px] text-text-secondary placeholder:text-text-quaternary
                focus:border-border-secondary focus:outline-none
                transition-colors
              "
              style={{ transitionDuration: 'var(--duration-fast)' }}
            />
          </div>
        </div>

        {/* Loading */}
        {loading && (
          <div className="space-y-2">
            {[1, 2, 3, 4, 5].map(i => (
              <div key={i} className="h-[56px] rounded-[7px] bg-bg-secondary/50 animate-pulse" />
            ))}
          </div>
        )}

        {/* Empty */}
        {!loading && filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center py-20">
            <Clock className="w-6 h-6 text-text-quaternary/30 mb-3" />
            <p className="text-[13px] text-text-quaternary text-center">
              {search ? 'No sessions match your search.' : 'No sessions yet.'}
            </p>
          </div>
        )}

        {/* Session groups */}
        {!loading && groups.length > 0 && (
          <div className="space-y-8">
            {groups.map(group => (
              <div key={group.label}>
                <h2 className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary mb-2 px-1">
                  {group.label}
                </h2>
                <div className="space-y-px">
                  {group.sessions.map(s => (
                    <SessionRow
                      key={s.id}
                      session={s}
                      onView={() => {
                        if (s.status === 'active' || s.status === 'paused') {
                          navigate(`/companion/session/${s.id}`)
                        } else {
                          navigate(`/sessions/${s.id}`)
                        }
                      }}
                      onDelete={e => handleDelete(s.id, e)}
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
// SessionRow
// ---------------------------------------------------------------------------

function SessionRow({
  session,
  onView,
  onDelete,
}: {
  session: SessionRecord
  onView: () => void
  onDelete: (e: React.MouseEvent) => void
}) {
  const Icon = getSessionIcon(session.title)
  const dateObj = session.date ? new Date(session.date) : null
  const timeStr = dateObj ? format(dateObj, 'h:mm a') : ''
  const preview = cleanSummaryPreview(session.summary_preview || '')

  return (
    <div
      onClick={onView}
      role="button"
      tabIndex={0}
      onKeyDown={e => { if (e.key === 'Enter') onView() }}
      className="
        w-full flex items-center gap-3 px-3 py-2.5
        rounded-[7px] text-left cursor-pointer
        hover:bg-hover active:bg-active
        transition-colors group
      "
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      {/* Icon */}
      <div className="w-8 h-8 rounded-[5px] bg-bg-secondary/60 flex items-center justify-center shrink-0">
        <Icon className="w-3.5 h-3.5 text-text-quaternary group-hover:text-text-tertiary transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }} />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="text-[12px] font-[510] text-text-secondary truncate group-hover:text-text transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
            {session.title || 'Untitled'}
          </span>
          {(session.status === 'active' || session.status === 'paused') && (
            <span className={`inline-flex items-center px-1.5 h-[16px] rounded-xs text-[9px] font-[510] shrink-0 ${session.status === 'active' ? 'bg-green-muted text-green' : 'bg-yellow-muted text-yellow'}`}>
              {session.status === 'active' ? 'Active' : 'Paused'}
            </span>
          )}
          {session.skill && (
            <span className="inline-flex items-center px-1.5 h-[16px] rounded-xs text-[9px] font-[510] bg-[rgba(255,245,235,0.04)] text-text-quaternary shrink-0">
              {session.skill}
            </span>
          )}
        </div>
        {preview && (
          <p className="text-[11px] text-text-quaternary truncate leading-relaxed">
            {preview}
          </p>
        )}
      </div>

      {/* Right: duration + time + actions */}
      <div className="flex items-center gap-2 shrink-0">
        {session.duration_seconds > 0 && (
          <span className="text-[10px] font-[510] text-text-quaternary tabular-nums">
            {formatDuration(session.duration_seconds)}
          </span>
        )}
        {timeStr && (
          <span className="text-[10px] text-text-quaternary tabular-nums min-w-[52px] text-right">
            {timeStr}
          </span>
        )}
        {session.status === 'paused' && (
          <span
            className="inline-flex items-center px-2 h-[20px] rounded-full text-[10px] font-[510] bg-accent/10 text-accent cursor-pointer hover:bg-accent/20 transition-colors"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            Resume
          </span>
        )}
        <button
          onClick={onDelete}
          className="
            h-6 w-6 rounded-xs
            inline-flex items-center justify-center
            text-transparent group-hover:text-text-quaternary
            hover:!text-red hover:bg-red-muted
            transition-colors cursor-pointer
          "
          style={{ transitionDuration: 'var(--duration-instant)' }}
          title="Delete"
        >
          <Trash2 className="w-3 h-3" />
        </button>
      </div>
    </div>
  )
}
