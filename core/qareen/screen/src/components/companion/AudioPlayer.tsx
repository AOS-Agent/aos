import { useState, useRef, useCallback, useEffect } from 'react'
import { Play, Pause, RotateCcw } from 'lucide-react'

// ---------------------------------------------------------------------------
// AudioPlayer — minimal audio player for session playback.
//
// Features: play/pause, seekable progress bar, duration display,
// playback speed (1x, 1.5x, 2x).
// ---------------------------------------------------------------------------

const SPEEDS = [1, 1.5, 2] as const

interface AudioPlayerProps {
  /** URL to the audio file (e.g. /meetings/{id}/audio) */
  src: string
  className?: string
}

export function AudioPlayer({ src, className = '' }: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const progressRef = useRef<HTMLDivElement>(null)

  const [playing, setPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [speedIdx, setSpeedIdx] = useState(0)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState(false)

  // ---- Audio event handlers ----

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return

    const onLoaded = () => {
      setDuration(audio.duration)
      setLoaded(true)
    }
    const onTimeUpdate = () => setCurrentTime(audio.currentTime)
    const onEnded = () => setPlaying(false)
    const onError = () => setError(true)

    audio.addEventListener('loadedmetadata', onLoaded)
    audio.addEventListener('timeupdate', onTimeUpdate)
    audio.addEventListener('ended', onEnded)
    audio.addEventListener('error', onError)

    return () => {
      audio.removeEventListener('loadedmetadata', onLoaded)
      audio.removeEventListener('timeupdate', onTimeUpdate)
      audio.removeEventListener('ended', onEnded)
      audio.removeEventListener('error', onError)
    }
  }, [])

  // ---- Controls ----

  const togglePlay = useCallback(() => {
    const audio = audioRef.current
    if (!audio) return
    if (playing) {
      audio.pause()
    } else {
      audio.play().catch(() => {})
    }
    setPlaying(!playing)
  }, [playing])

  const cycleSpeed = useCallback(() => {
    const next = (speedIdx + 1) % SPEEDS.length
    setSpeedIdx(next)
    if (audioRef.current) {
      audioRef.current.playbackRate = SPEEDS[next]
    }
  }, [speedIdx])

  const restart = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.currentTime = 0
      setCurrentTime(0)
    }
  }, [])

  const seek = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!progressRef.current || !audioRef.current || !duration) return
      const rect = progressRef.current.getBoundingClientRect()
      const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
      audioRef.current.currentTime = pct * duration
    },
    [duration],
  )

  // ---- Format helpers ----

  const fmt = (s: number) => {
    if (!isFinite(s)) return '0:00'
    const m = Math.floor(s / 60)
    const sec = Math.floor(s % 60)
    return `${m}:${sec.toString().padStart(2, '0')}`
  }

  const pct = duration > 0 ? (currentTime / duration) * 100 : 0

  if (error) {
    return (
      <div className={`flex items-center gap-2 px-3 py-2 rounded-sm bg-bg-secondary text-text-quaternary type-caption ${className}`}>
        Audio unavailable
      </div>
    )
  }

  return (
    <div className={`flex items-center gap-3 px-3 py-2.5 rounded-sm bg-bg-secondary border border-border ${className}`}>
      <audio ref={audioRef} src={src} preload="metadata" />

      {/* Play / Pause */}
      <button
        onClick={togglePlay}
        disabled={!loaded}
        className="
          h-8 w-8 rounded-full shrink-0
          inline-flex items-center justify-center
          bg-accent text-white
          disabled:opacity-30
          hover:bg-accent-hover
          transition-all duration-[var(--duration-instant)]
        "
        title={playing ? 'Pause' : 'Play'}
      >
        {playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5 ml-0.5" />}
      </button>

      {/* Time: current */}
      <span className="type-tiny text-text-tertiary font-mono tabular-nums shrink-0 w-[36px] text-right">
        {fmt(currentTime)}
      </span>

      {/* Progress bar */}
      <div
        ref={progressRef}
        onClick={seek}
        className="flex-1 h-5 flex items-center cursor-pointer group"
      >
        <div className="w-full h-1 rounded-full bg-bg-tertiary relative overflow-hidden">
          <div
            className="absolute inset-y-0 left-0 bg-accent rounded-full transition-[width] duration-100"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {/* Time: total */}
      <span className="type-tiny text-text-quaternary font-mono tabular-nums shrink-0 w-[36px]">
        {fmt(duration)}
      </span>

      {/* Restart */}
      <button
        onClick={restart}
        className="
          h-6 w-6 rounded-xs shrink-0
          inline-flex items-center justify-center
          text-text-quaternary hover:text-text-secondary hover:bg-hover
          transition-all duration-[var(--duration-instant)]
        "
        title="Restart"
      >
        <RotateCcw className="w-3 h-3" />
      </button>

      {/* Speed */}
      <button
        onClick={cycleSpeed}
        className="
          h-6 px-1.5 rounded-xs shrink-0
          inline-flex items-center justify-center
          text-text-quaternary hover:text-text-secondary hover:bg-hover
          text-[10px] font-semibold tabular-nums
          transition-all duration-[var(--duration-instant)]
        "
        title="Playback speed"
      >
        {SPEEDS[speedIdx]}x
      </button>
    </div>
  )
}
