import { useRef, useEffect, useState, useCallback, type FormEvent } from 'react'
import { ArrowDown, Activity, HeartPulse, Send, Mic, MicOff } from 'lucide-react'
import { useVoiceCapture } from '@/hooks/useVoiceCapture'
import { format } from 'date-fns'
import { useCompanionStore } from '@/store/companion'
import type { StreamItem } from '@/store/companion'
import { TranscriptSegment } from './TranscriptSegment'
import { BriefingCard } from './BriefingCard'

// ---------------------------------------------------------------------------
// StreamColumn — center column, the live stream.
//
// Contains transcript segments, system events, briefings, health events.
// Scroll behavior: sticky-to-bottom when at bottom, "Jump to live" pill
// appears when scrolled up.
// ---------------------------------------------------------------------------

export function StreamColumn() {
  const stream = useCompanionStore((s) => s.stream)
  const segments = useCompanionStore((s) => s.segments)
  const briefing = useCompanionStore((s) => s.briefing)
  const addCard = useCompanionStore((s) => s.addCard)
  const addSegment = useCompanionStore((s) => s.addSegment)

  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const [isAtBottom, setIsAtBottom] = useState(true)
  const [inputText, setInputText] = useState('')
  const { active: micActive, toggle: toggleMic, error: micError } = useVoiceCapture()
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Submit text input to the companion intelligence engine
  const handleSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault()
      const text = inputText.trim()
      if (!text || isSubmitting) return

      setIsSubmitting(true)
      setInputText('')

      // Optimistic: add transcript segment immediately
      const segmentId = crypto.randomUUID()
      addSegment({
        id: segmentId,
        speaker: 'You',
        text,
        timestamp: new Date().toISOString(),
        isProvisional: false,
      })

      try {
        const res = await fetch('/companion/input', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, source: 'text' }),
        })
        if (res.ok) {
          const data = await res.json()
          // If a card was generated, add it to the store
          // (SSE will also push it, but this gives immediate feedback)
          if (data.card) {
            addCard(data.card)
          }
        }
      } catch {
        // Network error — the optimistic segment stays
      } finally {
        setIsSubmitting(false)
        inputRef.current?.focus()
      }
    },
    [inputText, isSubmitting, addSegment, addCard],
  )

  // Check if user is at the bottom of the scroll
  const checkScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const threshold = 60
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < threshold
    setIsAtBottom(atBottom)
  }, [])

  // Auto-scroll to bottom when new items arrive and user is at bottom
  // Debounced to avoid jitter with rapid updates
  const scrollToBottom = useCallback(() => {
    if (isAtBottom && scrollRef.current) {
      requestAnimationFrame(() => {
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
      })
    }
  }, [isAtBottom])

  useEffect(() => {
    const timer = setTimeout(scrollToBottom, 50)
    return () => clearTimeout(timer)
  }, [stream.length, segments.length, scrollToBottom])

  const jumpToLive = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [])

  // Build a merged timeline: briefing + transcript segments + system events
  // Transcript segments live in their own array; system events come from stream.
  // We merge them by timestamp for correct ordering.
  const mergedItems = buildTimeline(stream, segments, briefing)

  return (
    <div className="relative flex-1 min-h-0 flex flex-col">
      <div
        ref={scrollRef}
        onScroll={checkScroll}
        className="flex-1 overflow-y-auto"
      >
        {mergedItems.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full">
            <p className="type-caption text-text-quaternary">
              Type a command, task, or thought below...
            </p>
          </div>
        ) : (
          <div className="py-2">
            {mergedItems.map((item) => (
              <StreamItemRenderer key={item.key} item={item} />
            ))}
          </div>
        )}
      </div>

      {/* Jump to live pill */}
      {!isAtBottom && (
        <button
          onClick={jumpToLive}
          className="
            absolute bottom-14 left-1/2 -translate-x-1/2
            inline-flex items-center gap-1.5
            px-3 h-7 rounded-full
            bg-bg-tertiary text-text-secondary
            text-[11px] font-medium
            shadow-medium border border-border-secondary
            hover:bg-bg-quaternary
            transition-all duration-[var(--duration-fast)]
            z-10
          "
        >
          <ArrowDown className="w-3 h-3" />
          Jump to live
        </button>
      )}

      {/* Input bar — text + mic */}
      <div className="border-t border-border p-3 bg-bg-panel">
        {micError && (
          <p className="text-[11px] text-red mb-2 px-1">{micError}</p>
        )}
        <form onSubmit={handleSubmit} className="flex gap-2">
          {/* Mic button */}
          <button
            type="button"
            onClick={() => toggleMic()}
            className={`h-9 w-9 shrink-0 rounded-[5px] inline-flex items-center justify-center transition-all duration-[80ms] ${
              micActive
                ? 'bg-red text-white animate-pulse'
                : 'bg-bg-secondary border border-border text-text-tertiary hover:text-text hover:bg-bg-tertiary'
            }`}
            title={micActive ? 'Stop listening' : 'Start listening'}
          >
            {micActive ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
          </button>
          <input
            ref={inputRef}
            type="text"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            placeholder={micActive ? 'Listening... or type here' : 'Type a command, task, or thought...'}
            className="flex-1 h-9 px-3 bg-bg-secondary border border-border rounded-[5px] text-[13px] text-text placeholder:text-text-quaternary focus:border-accent/30 focus:outline-none transition-colors duration-[80ms]"
            disabled={isSubmitting}
          />
          <button
            type="submit"
            disabled={!inputText.trim() || isSubmitting}
            className="h-9 px-4 bg-accent text-white rounded-[5px] text-[13px] font-[510] disabled:opacity-40 hover:bg-accent-hover transition-colors duration-[80ms] inline-flex items-center gap-1.5"
          >
            <Send className="w-3.5 h-3.5" />
            Send
          </button>
        </form>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Timeline building
// ---------------------------------------------------------------------------

interface TimelineEntry {
  key: string
  type: 'transcript' | 'briefing' | 'activity' | 'health' | 'system'
  timestamp: string
  data: Record<string, unknown>
}

function buildTimeline(
  stream: StreamItem[],
  segments: { id: string; speaker: string; text: string; timestamp: string; isProvisional: boolean }[],
  briefing: import('@/store/companion').Briefing | null,
): TimelineEntry[] {
  const items: TimelineEntry[] = []

  // Add briefing at the top if present
  if (briefing) {
    items.push({
      key: `briefing-${briefing.id}`,
      type: 'briefing',
      timestamp: briefing.timestamp,
      data: briefing as unknown as Record<string, unknown>,
    })
  }

  // Add transcript segments
  for (const seg of segments) {
    items.push({
      key: `transcript-${seg.id}`,
      type: 'transcript',
      timestamp: seg.timestamp,
      data: seg as unknown as Record<string, unknown>,
    })
  }

  // Add non-transcript stream events (activity, health, system)
  for (const event of stream) {
    if (event.type === 'transcript' || event.type === 'briefing') continue
    items.push({
      key: `stream-${event.id}`,
      type: event.type as 'activity' | 'health' | 'system',
      timestamp: event.timestamp,
      data: event.data,
    })
  }

  // Sort by timestamp
  items.sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
  )

  return items
}

// ---------------------------------------------------------------------------
// StreamItemRenderer
// ---------------------------------------------------------------------------

function StreamItemRenderer({ item }: { item: TimelineEntry }) {
  switch (item.type) {
    case 'transcript': {
      const d = item.data as unknown as {
        speaker: string
        text: string
        timestamp: string
        isProvisional: boolean
      }
      return (
        <TranscriptSegment
          speaker={d.speaker}
          text={d.text}
          timestamp={d.timestamp}
          isProvisional={d.isProvisional}
        />
      )
    }

    case 'briefing': {
      const b = item.data as unknown as import('@/store/companion').Briefing
      return <BriefingCard briefing={b} />
    }

    case 'activity':
      return <SystemEventRow icon={<Activity className="w-3 h-3" />} item={item} />

    case 'health':
      return (
        <SystemEventRow icon={<HeartPulse className="w-3 h-3" />} item={item} />
      )

    case 'system':
      return <SystemEventRow icon={<Activity className="w-3 h-3" />} item={item} />

    default:
      return null
  }
}

// ---------------------------------------------------------------------------
// System event row — compact single-line event in the stream
// ---------------------------------------------------------------------------

function SystemEventRow({
  icon,
  item,
}: {
  icon: React.ReactNode
  item: TimelineEntry
}) {
  const message =
    (item.data.message as string) ||
    (item.data.summary as string) ||
    ''
  const source = (item.data.source as string) || ''

  const time = (() => {
    try {
      return format(new Date(item.timestamp), 'HH:mm')
    } catch {
      return ''
    }
  })()

  if (!message) return null

  return (
    <div className="flex items-start gap-2 px-4 py-1 group">
      <span className="text-text-quaternary mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0 flex-1">
        <p className="type-caption text-text-tertiary leading-relaxed">
          {source && (
            <span className="text-text-quaternary font-mono text-[10px] mr-1.5">
              {source}
            </span>
          )}
          {message}
        </p>
      </div>
      <span className="type-tiny text-text-quaternary shrink-0 mt-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
        {time}
      </span>
    </div>
  )
}
