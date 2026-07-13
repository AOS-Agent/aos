import { memo, useMemo } from 'react'
import { CreditCard, Bot, Loader2 } from 'lucide-react'

// ---------------------------------------------------------------------------
// SessionTray — compact bottom bar showing session status.
//
// Three sections: cards count | agents count | voice state + timer.
// Glass background, 36px tall, full width. Follows glass pill pattern
// from DESIGN.md but applied as a bar (rounded-[5px] not pill).
// ---------------------------------------------------------------------------

interface SessionTrayProps {
  pendingCards: number
  activeAgents: number
  sessionSeconds: number
  voiceState: 'idle' | 'listening' | 'processing' | 'speaking'
  onExpandCards?: () => void
  onExpandAgents?: () => void
}

export const SessionTray = memo(function SessionTray({
  pendingCards,
  activeAgents,
  sessionSeconds,
  voiceState,
  onExpandCards,
  onExpandAgents,
}: SessionTrayProps) {
  const timer = useMemo(() => {
    const mins = Math.floor(sessionSeconds / 60)
    const secs = sessionSeconds % 60
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
  }, [sessionSeconds])

  return (
    <div
      className="
        shrink-0 w-full h-9
        flex items-center
        bg-[var(--glass-bg)] backdrop-blur-[12px]
        border border-[var(--glass-border)]
        rounded-[5px]
        shadow-[var(--glass-shadow)]
      "
    >
      {/* Left: Cards count */}
      <button
        onClick={onExpandCards}
        className={`
          flex-1 flex items-center justify-center gap-1.5
          h-full
          transition-colors duration-[80ms]
          ${onExpandCards ? 'cursor-pointer hover:bg-hover/50' : 'cursor-default'}
        `}
      >
        <CreditCard className={`w-3 h-3 ${pendingCards > 0 ? 'text-accent' : 'text-text-quaternary'}`} />
        <span className={`text-[11px] font-[510] ${pendingCards > 0 ? 'text-text-secondary' : 'text-text-quaternary'}`}>
          {pendingCards} {pendingCards === 1 ? 'card' : 'cards'}
        </span>
        {pendingCards > 0 && (
          <span className="w-1.5 h-1.5 rounded-full bg-accent shrink-0" />
        )}
      </button>

      {/* Divider */}
      <div className="w-px h-4 bg-border/60 shrink-0" />

      {/* Center: Agents count */}
      <button
        onClick={onExpandAgents}
        className={`
          flex-1 flex items-center justify-center gap-1.5
          h-full
          transition-colors duration-[80ms]
          ${onExpandAgents ? 'cursor-pointer hover:bg-hover/50' : 'cursor-default'}
        `}
      >
        {activeAgents > 0 ? (
          <Loader2 className="w-3 h-3 text-accent animate-spin" />
        ) : (
          <Bot className="w-3 h-3 text-text-quaternary" />
        )}
        <span className={`text-[11px] font-[510] ${activeAgents > 0 ? 'text-text-secondary' : 'text-text-quaternary'}`}>
          {activeAgents} {activeAgents === 1 ? 'agent' : 'agents'}
        </span>
      </button>

      {/* Divider */}
      <div className="w-px h-4 bg-border/60 shrink-0" />

      {/* Right: Voice state + timer */}
      <div className="flex-1 flex items-center justify-center gap-2 h-full">
        <VoiceDot state={voiceState} />
        <span className="text-[11px] text-text-quaternary font-mono tabular-nums">
          {timer}
        </span>
      </div>

      <style>{`
        @keyframes tray-pulse {
          0%, 100% { opacity: 0.4; }
          50% { opacity: 1; }
        }
        @keyframes tray-waveform {
          0% { transform: scaleY(0.4); }
          50% { transform: scaleY(1); }
          100% { transform: scaleY(0.4); }
        }
      `}</style>
    </div>
  )
})

// ---------------------------------------------------------------------------
// VoiceDot — voice state indicator with label
// ---------------------------------------------------------------------------

function VoiceDot({ state }: { state: SessionTrayProps['voiceState'] }) {
  switch (state) {
    case 'idle':
      return <div className="w-1.5 h-1.5 rounded-full bg-text-quaternary opacity-40" />

    case 'listening':
      return (
        <div className="flex items-center gap-1.5">
          <div
            className="w-1.5 h-1.5 rounded-full bg-red"
            style={{ animation: 'tray-pulse 1200ms ease-in-out infinite' }}
          />
          <span className="text-[11px] font-[450] text-red">Listening</span>
        </div>
      )

    case 'processing':
      return (
        <div className="flex items-center gap-1.5">
          <div
            className="w-1.5 h-1.5 rounded-full bg-accent"
            style={{ animation: 'tray-pulse 900ms ease-in-out infinite' }}
          />
          <span className="text-[11px] font-[450] text-accent">Processing</span>
        </div>
      )

    case 'speaking':
      return (
        <div className="flex items-center gap-1.5">
          <div className="flex items-center gap-[2px]">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="w-[2px] h-2.5 rounded-full bg-accent origin-center"
                style={{
                  animation: 'tray-waveform 600ms ease-in-out infinite',
                  animationDelay: `${i * 120}ms`,
                }}
              />
            ))}
          </div>
          <span className="text-[11px] font-[450] text-accent">Speaking</span>
        </div>
      )
  }
}
