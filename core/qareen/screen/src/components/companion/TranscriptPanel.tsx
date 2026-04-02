import { useRef, useEffect, useState, useCallback, memo } from 'react'
import { ArrowDown, Mic } from 'lucide-react'
import { format } from 'date-fns'
import { useCompanionStore, type TranscriptSegment } from '@/store/companion'
import { WaveformStrip } from './WaveformStrip'

// ---------------------------------------------------------------------------
// TranscriptPanel — left column (40%) during conversation sessions.
//
// Scrolling transcript with speaker labels, provisional text rendering,
// auto-scroll, and collapse toggle to waveform strip.
// Floats on the canvas background — no solid panel, no header bar.
// Speaker names in serif (Garamond). Timestamps in sans (Inter).
// ---------------------------------------------------------------------------

// Operator display name — replace raw pipeline labels
const OPERATOR_NAME = 'Hisham'

/** Normalize speaker labels — "operator" and "You" both map to the operator name. */
function normalizeSpeaker(speaker: string): string {
  const lower = speaker.toLowerCase()
  if (lower === 'operator' || lower === 'you') return OPERATOR_NAME
  return speaker
}

// Speaker color palette — warm, distinguishable
const SPEAKER_COLORS: Record<string, string> = {
  [OPERATOR_NAME]: 'text-accent',
  You: 'text-accent',
  operator: 'text-accent',
}
const FALLBACK_COLORS = [
  'text-blue',
  'text-purple',
  'text-teal',
  'text-green',
  'text-pink',
  'text-yellow',
]

function getSpeakerColor(speaker: string, speakerMap: Map<string, string>): string {
  if (SPEAKER_COLORS[speaker]) return SPEAKER_COLORS[speaker]
  if (speakerMap.has(speaker)) return speakerMap.get(speaker)!
  const color = FALLBACK_COLORS[speakerMap.size % FALLBACK_COLORS.length]
  speakerMap.set(speaker, color)
  return color
}

export function TranscriptPanel() {
  const segments = useCompanionStore((s) => s.segments)
  const collapsed = useCompanionStore((s) => s.transcriptCollapsed)
  const voiceState = useCompanionStore((s) => s.voiceState)

  const scrollRef = useRef<HTMLDivElement>(null)
  const [isAtBottom, setIsAtBottom] = useState(true)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const speakerMapRef = useRef(new Map<string, string>())

  // Check scroll position
  const checkScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
    setIsAtBottom(atBottom)
  }, [])

  // Auto-scroll when at bottom
  useEffect(() => {
    if (isAtBottom && scrollRef.current) {
      requestAnimationFrame(() => {
        scrollRef.current?.scrollTo({
          top: scrollRef.current.scrollHeight,
          behavior: 'smooth',
        })
      })
    }
  }, [segments.length, isAtBottom])

  const jumpToBottom = useCallback(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }, [])

  // Collapsed view: waveform strip
  if (collapsed) {
    return (
      <div className="w-12 shrink-0 flex flex-col items-center pt-3">
        <WaveformStrip active={voiceState === 'listening' || voiceState === 'speaking'} />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full relative">
      {/* Segments — no header, transcript is self-evident */}
      <div
        ref={scrollRef}
        onScroll={checkScroll}
        className="flex-1 overflow-y-auto"
      >
        {segments.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-4 px-8">
            <div className="w-14 h-14 rounded-full bg-bg-secondary/50 flex items-center justify-center">
              <Mic className="w-5 h-5 text-text-quaternary/60" />
            </div>
            <p className="text-[15px] text-text-quaternary/80 text-center leading-relaxed" style={{ fontFamily: 'var(--font-serif)' }}>
              Tap the mic or start speaking
            </p>
          </div>
        ) : (
          <div className="py-4 px-1">
            {segments.map((seg, idx) => {
              // Normalize speaker for display
              const displaySpeaker = normalizeSpeaker(seg.speaker)
              const prevDisplay = idx > 0 ? normalizeSpeaker(segments[idx - 1].speaker) : null
              const showLabel = displaySpeaker !== prevDisplay

              return (
                <SegmentRow
                  key={seg.id}
                  segment={{ ...seg, speaker: displaySpeaker }}
                  showSpeaker={showLabel}
                  speakerColor={getSpeakerColor(displaySpeaker, speakerMapRef.current)}
                  isHovered={hoveredId === seg.id}
                  onHover={() => setHoveredId(seg.id)}
                  onLeave={() => setHoveredId(null)}
                />
              )
            })}
          </div>
        )}
      </div>

      {/* Jump to bottom — glass pill style */}
      {!isAtBottom && (
        <button
          onClick={jumpToBottom}
          className="
            absolute bottom-4 left-1/2 -translate-x-1/2
            inline-flex items-center gap-1.5
            px-3 h-7 rounded-full
            bg-bg-secondary/60 backdrop-blur-md
            text-text-secondary text-[11px] font-medium
            shadow-[0_2px_12px_rgba(0,0,0,0.3)]
            border border-border/40
            hover:bg-bg-tertiary/70 hover:text-text
            transition-all duration-[150ms]
            z-10 cursor-pointer
          "
        >
          <ArrowDown className="w-3 h-3" />
          Latest
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// SegmentRow — a single transcript segment
//
// Speaker names use serif font (Garamond) — they're content, not chrome.
// Timestamps stay in Inter (sans). Text gets generous line height
// for a book-like reading experience.
// ---------------------------------------------------------------------------

interface SegmentRowProps {
  segment: TranscriptSegment
  showSpeaker: boolean
  speakerColor: string
  isHovered: boolean
  onHover: () => void
  onLeave: () => void
}

const SegmentRow = memo(function SegmentRow({
  segment,
  showSpeaker,
  speakerColor,
  isHovered,
  onHover,
  onLeave,
}: SegmentRowProps) {
  const textRef = useRef<HTMLParagraphElement>(null)
  const prevTextRef = useRef(segment.text)

  // Direct DOM update for provisional segments
  useEffect(() => {
    if (segment.isProvisional && textRef.current && segment.text !== prevTextRef.current) {
      textRef.current.textContent = segment.text
      prevTextRef.current = segment.text
    }
  }, [segment.text, segment.isProvisional])

  const time = (() => {
    try {
      return format(new Date(segment.timestamp), 'HH:mm')
    } catch {
      return ''
    }
  })()

  return (
    <div
      className={`
        px-4 group relative
        rounded-md
        transition-colors duration-[80ms]
        ${isHovered ? 'bg-hover' : ''}
        ${showSpeaker ? 'pt-5' : 'pt-1'}
        ${segment.isProvisional ? '' : 'animate-[segment-in_180ms_ease-out]'}
      `}
      onMouseEnter={onHover}
      onMouseLeave={onLeave}
    >
      {/* Speaker label — serif font for content */}
      {showSpeaker && (
        <div className="flex items-baseline gap-2 mb-1">
          <span
            className={`text-[14px] font-semibold ${speakerColor}`}
            style={{ fontFamily: 'var(--font-serif)' }}
          >
            {segment.speaker}
          </span>
        </div>
      )}

      {/* Text — generous line height, warm and readable */}
      <p
        ref={textRef}
        className={`
          text-[14px] leading-[1.7] pr-10 pb-1
          ${segment.isProvisional ? 'text-text-quaternary' : 'text-text-secondary'}
        `}
        style={{ fontFamily: 'var(--font-serif)' }}
      >
        {segment.text}
      </p>

      {/* Timestamp on hover — sans-serif, subtle */}
      {isHovered && time && (
        <span className="
          absolute right-4 top-1/2 -translate-y-1/2
          text-[10px] font-[510] text-text-quaternary/70 tabular-nums
          transition-opacity duration-[80ms]
        ">
          {time}
        </span>
      )}

      <style>{`
        @keyframes segment-in {
          from { opacity: 0; transform: translateY(2px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  )
})
