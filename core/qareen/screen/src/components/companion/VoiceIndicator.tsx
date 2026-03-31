import { useEffect, useRef, useCallback } from 'react'
import { useCompanionStore, type VoiceState } from '@/store/companion'
import { useAudioAnalyser } from '@/hooks/useAudioAnalyser'

// ---------------------------------------------------------------------------
// VoiceIndicator — 32px tall bar at top of stream column.
//
// States:
//   idle       — 3 dots, muted, no animation
//   listening  — 5 bars, heights driven by mic AudioContext at 30fps
//   processing — 3 dots, slow pulse (opacity 0.4 → 1.0, 900ms)
//   speaking   — 5 bars, heights driven by TTS audio analyser
// ---------------------------------------------------------------------------

const BAR_COUNT = 5

export function VoiceIndicator() {
  const voiceState = useCompanionStore((s) => s.voiceState)
  const barsRef = useRef<(HTMLDivElement | null)[]>([])
  const rafRef = useRef<number>(0)
  const { start, stop, getAmplitude } = useAudioAnalyser()
  const activeRef = useRef(false)

  // Animate bars at ~30fps using amplitude
  const animateBars = useCallback(() => {
    if (!activeRef.current) return
    const amplitude = getAmplitude()

    for (let i = 0; i < BAR_COUNT; i++) {
      const bar = barsRef.current[i]
      if (!bar) continue
      // Each bar gets a slightly different multiplier for organic feel
      const multiplier = 1 + Math.sin((i / BAR_COUNT) * Math.PI) * 0.6
      const height = Math.max(4, Math.min(20, amplitude * multiplier * 80))
      bar.style.height = `${height}px`
    }

    rafRef.current = requestAnimationFrame(animateBars)
  }, [getAmplitude])

  // Start/stop audio analysis based on voice state
  useEffect(() => {
    if (voiceState === 'listening' || voiceState === 'speaking') {
      activeRef.current = true
      start().then((ok) => {
        if (ok) {
          rafRef.current = requestAnimationFrame(animateBars)
        }
      })
    } else {
      activeRef.current = false
      cancelAnimationFrame(rafRef.current)
      stop()
      // Reset bars to resting height
      for (const bar of barsRef.current) {
        if (bar) bar.style.height = '4px'
      }
    }

    return () => {
      activeRef.current = false
      cancelAnimationFrame(rafRef.current)
    }
  }, [voiceState, start, stop, animateBars])

  return (
    <div className="h-8 flex items-center justify-center gap-[3px] shrink-0">
      {voiceState === 'idle' && <IdleDots />}
      {voiceState === 'processing' && <ProcessingDots />}
      {(voiceState === 'listening' || voiceState === 'speaking') && (
        <LiveBars barsRef={barsRef} state={voiceState} />
      )}
    </div>
  )
}

// --- Sub-components ---

function IdleDots() {
  return (
    <div className="flex items-center gap-1.5">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="w-1.5 h-1.5 rounded-full bg-text-quaternary opacity-40"
        />
      ))}
    </div>
  )
}

function ProcessingDots() {
  return (
    <div className="flex items-center gap-1.5">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="w-1.5 h-1.5 rounded-full bg-accent"
          style={{
            animation: 'voice-pulse 900ms ease-in-out infinite',
            animationDelay: `${i * 150}ms`,
          }}
        />
      ))}
      <style>{`
        @keyframes voice-pulse {
          0%, 100% { opacity: 0.4; }
          50% { opacity: 1; }
        }
      `}</style>
    </div>
  )
}

function LiveBars({
  barsRef,
  state,
}: {
  barsRef: React.MutableRefObject<(HTMLDivElement | null)[]>
  state: VoiceState
}) {
  const color = state === 'speaking' ? 'bg-accent' : 'bg-green'

  return (
    <div className="flex items-center gap-[2px]">
      {Array.from({ length: BAR_COUNT }).map((_, i) => (
        <div
          key={i}
          ref={(el) => { barsRef.current[i] = el }}
          className={`w-[3px] rounded-full ${color}`}
          style={{
            height: '4px',
            transition: 'height 33ms linear',
          }}
        />
      ))}
    </div>
  )
}
