import { useRef, useEffect, memo } from 'react'
import { format } from 'date-fns'

// ---------------------------------------------------------------------------
// TranscriptSegment — a single spoken segment in the stream.
//
// Provisional (still being transcribed): text-quaternary, updates via
// textContent mutation to avoid React re-render cost.
//
// Finalized: text-secondary, fades in over 120ms.
// ---------------------------------------------------------------------------

interface TranscriptSegmentProps {
  speaker: string
  text: string
  timestamp: string
  isProvisional: boolean
}

export const TranscriptSegment = memo(function TranscriptSegment({
  speaker,
  text,
  timestamp,
  isProvisional,
}: TranscriptSegmentProps) {
  const textRef = useRef<HTMLParagraphElement>(null)
  const prevTextRef = useRef(text)

  // For provisional segments, update textContent directly to avoid DOM churn
  useEffect(() => {
    if (isProvisional && textRef.current && text !== prevTextRef.current) {
      textRef.current.textContent = text
      prevTextRef.current = text
    }
  }, [text, isProvisional])

  const time = (() => {
    try {
      return format(new Date(timestamp), 'HH:mm')
    } catch {
      return ''
    }
  })()

  return (
    <div
      className={`
        px-4 py-1.5 group
        ${isProvisional ? '' : 'animate-[transcript-in_120ms_ease-out]'}
      `}
    >
      {/* Speaker + timestamp */}
      <div className="flex items-baseline gap-2 mb-0.5">
        <span
          className={`type-label ${
            speaker === 'You' ? 'text-accent' : 'text-text-secondary'
          }`}
        >
          {speaker}
        </span>
        <span className="type-tiny text-text-quaternary">{time}</span>
      </div>

      {/* Text */}
      <p
        ref={textRef}
        className={`type-body leading-relaxed ${
          isProvisional ? 'text-text-quaternary' : 'text-text-secondary'
        }`}
      >
        {text}
      </p>

      <style>{`
        @keyframes transcript-in {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
      `}</style>
    </div>
  )
})
