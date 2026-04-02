import { useRef, useEffect, useCallback } from 'react'

// ---------------------------------------------------------------------------
// WaveformStrip — collapsed transcript view.
//
// Thin animated waveform bars showing audio is being captured.
// When inactive, shows a thin static line.
// ---------------------------------------------------------------------------

const BAR_COUNT = 3

interface WaveformStripProps {
  active: boolean
}

export function WaveformStrip({ active }: WaveformStripProps) {
  const barsRef = useRef<(HTMLDivElement | null)[]>([])
  const rafRef = useRef<number>(0)
  const activeRef = useRef(active)

  useEffect(() => {
    activeRef.current = active
  }, [active])

  const animate = useCallback(() => {
    if (!activeRef.current) return

    for (let i = 0; i < BAR_COUNT; i++) {
      const bar = barsRef.current[i]
      if (!bar) continue
      // Organic pseudo-random heights
      const phase = Date.now() / 300 + i * 1.3
      const height = 6 + Math.sin(phase) * 4 + Math.sin(phase * 2.3) * 3
      bar.style.height = `${Math.max(3, height)}px`
    }

    rafRef.current = requestAnimationFrame(animate)
  }, [])

  useEffect(() => {
    if (active) {
      rafRef.current = requestAnimationFrame(animate)
    } else {
      cancelAnimationFrame(rafRef.current)
      // Reset to resting
      for (const bar of barsRef.current) {
        if (bar) bar.style.height = '3px'
      }
    }
    return () => cancelAnimationFrame(rafRef.current)
  }, [active, animate])

  if (!active) {
    return (
      <div className="w-full flex items-center justify-center py-2">
        <div className="w-[2px] h-full min-h-[40px] bg-border rounded-full" />
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center gap-[2px] py-2">
      {Array.from({ length: BAR_COUNT }).map((_, i) => (
        <div
          key={i}
          ref={(el) => { barsRef.current[i] = el }}
          className="w-[3px] rounded-full bg-green"
          style={{
            height: '3px',
            transition: 'height 33ms linear',
          }}
        />
      ))}
    </div>
  )
}
