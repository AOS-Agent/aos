import { useState, useCallback, useEffect, useRef, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { Mic, MicOff, Clock } from 'lucide-react'
import { Orb } from '@/components/companion/Orb'
import { OrbitalPills, type PillMode, MODES } from '@/components/companion/OrbitalPills'
import { useAudioAnalyser } from '@/hooks/useAudioAnalyser'
import { useOperator } from '@/hooks/useConfig'
import {
  calcPrayerSchedule,
  currentPrayerPeriod,
  DEFAULT_COORDS,
  type PrayerPeriod,
} from '@/lib/prayer'

// ---------------------------------------------------------------------------
// SessionLauncher — the Qareen presence.
//
// Orb IS the background (blurred, prayer-colored, voice-reactive).
// Web Speech API handles transcription AND voice detection (single mic stream).
// No useAudioAnalyser — Speech Recognition owns the mic exclusively.
// ---------------------------------------------------------------------------

// Prayer period → orb color pairs
const PERIOD_COLORS: Record<PrayerPeriod, [string, string]> = {
  'last-third': ['#2A1F4E', '#4A3570'],
  'fajr':       ['#5C3D7A', '#8B5A6B'],
  'sunrise':    ['#C4692A', '#E8943D'],
  'duha':       ['#D9730D', '#E8943D'],
  'zawal':      ['#D4920F', '#E8B84D'],
  'dhuhr':      ['#D4920F', '#E8B84D'],
  'asr':        ['#C47020', '#D9883A'],
  'pre-maghrib':['#B55A18', '#D46830'],
  'maghrib':    ['#9A3E1A', '#C45530'],
  'isha':       ['#3D2A1E', '#6B4530'],
}

// Trigger phrases → 4 modes: Talk, Meet, Plan, Clear
const TRIGGERS: Array<{ patterns: RegExp[]; skill: string }> = [
  {
    skill: 'thinking',
    patterns: [
      /\bthink/i, /\bbrainstorm/i, /\bramble/i, /\bprocess/i,
      /\btalk/i, /\bchat/i, /\bdiscuss/i, /\bexplore/i,
      /\bfigure.*out/i, /\bwork.*through/i,
      /\bdecid/i, /\bdecis/i, /\bchoose/i, /\bchoice/i,
      /\bweigh/i, /\bcompar/i, /\boption/i, /\bpros.*cons/i,
    ],
  },
  {
    skill: 'meeting',
    patterns: [
      /\bmeeting/i, /\bcall\b/i, /\bmeet\b/i,
      /\brecord/i, /\bconference/i, /\bjoin/i,
    ],
  },
  {
    skill: 'planning',
    patterns: [
      /\bplan/i, /\bscope/i, /\bbreak.*down/i, /\bprioritize/i,
      /\bdecompose/i, /\broadmap/i, /\bstrateg/i,
      /\borganize/i, /\bwhat.*next/i, /\bschedul/i,
    ],
  },
  {
    skill: 'email',
    patterns: [
      /\bdeclutter/i, /\binbox/i, /\btriage/i, /\bemail/i,
      /\bclean/i, /\bclear/i, /\bsort/i, /\bgmail/i,
    ],
  },
]

function detectTrigger(text: string): string | null {
  for (const t of TRIGGERS) {
    for (const p of t.patterns) {
      if (p.test(text)) return t.skill
    }
  }
  return null
}

// Glass chime
function playGlassChime() {
  try {
    const ctx = new AudioContext()
    const now = ctx.currentTime
    const osc1 = ctx.createOscillator()
    const osc2 = ctx.createOscillator()
    const gain1 = ctx.createGain()
    const gain2 = ctx.createGain()
    osc1.type = 'sine'; osc1.frequency.value = 1046.5
    osc2.type = 'sine'; osc2.frequency.value = 1568
    gain1.gain.setValueAtTime(0, now)
    gain1.gain.linearRampToValueAtTime(0.15, now + 0.008)
    gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.6)
    gain2.gain.setValueAtTime(0, now)
    gain2.gain.linearRampToValueAtTime(0.08, now + 0.012)
    gain2.gain.exponentialRampToValueAtTime(0.001, now + 0.5)
    osc1.connect(gain1).connect(ctx.destination)
    osc2.connect(gain2).connect(ctx.destination)
    osc1.start(now); osc2.start(now)
    osc1.stop(now + 0.7); osc2.stop(now + 0.6)
    setTimeout(() => ctx.close(), 1000)
  } catch { /* audio not available */ }
}

// Web Speech API factory
function createSpeechRecognition(): any | null {
  const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
  if (!SR) return null
  const recognition = new SR()
  recognition.continuous = true
  recognition.interimResults = true
  recognition.lang = 'en-US'
  recognition.maxAlternatives = 1
  return recognition
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface SessionLauncherProps {
  onStartSession: (type: string, text?: string) => void
}

function getGreeting(period: PrayerPeriod): string {
  switch (period) {
    case 'fajr':
    case 'last-third':
      return 'Peace be upon this hour'
    case 'sunrise':
    case 'duha':
      return 'Good morning'
    case 'dhuhr':
      return 'Good afternoon'
    case 'asr':
      return 'Good afternoon'
    case 'maghrib':
      return 'Good evening'
    case 'isha':
      return 'Good evening'
    default:
      return 'Assalamu alaikum'
  }
}

// Work out WHY the mic is unavailable so the UI can say what to do about it,
// instead of a bare red "Mic unavailable". Avoids a second getUserMedia prompt.
async function classifyMicFailure(): Promise<string> {
  if (typeof window === 'undefined') return 'Mic unavailable'
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    return 'Open the dashboard on localhost or over https:// — browsers block the mic otherwise'
  }
  try {
    const status = await navigator.permissions?.query({ name: 'microphone' as PermissionName })
    if (status?.state === 'denied') {
      return 'Microphone blocked — allow it for this site in your browser, then retry'
    }
  } catch { /* permissions API unavailable — fall through */ }
  try {
    const devices = await navigator.mediaDevices.enumerateDevices()
    if (!devices.some(d => d.kind === 'audioinput')) {
      return 'No microphone found — connect one and retry'
    }
  } catch { /* enumerateDevices unavailable — fall through */ }
  return 'Microphone unavailable — check that no other app is using it, then retry'
}

export function SessionLauncher({ onStartSession }: SessionLauncherProps) {
  const { data: op } = useOperator()
  const [hoveredPill, setHoveredPill] = useState<string | null>(null)
  const [micConnected, setMicConnected] = useState(false)
  const [micReason, setMicReason] = useState('')
  const [speechAvailable, setSpeechAvailable] = useState(false)
  const [micMuted, setMicMuted] = useState(false)
  const micMutedRef = useRef(false)
  const [isSpeaking, setIsSpeaking] = useState(false)
  const [liveText, setLiveText] = useState('')
  const [finalText, setFinalText] = useState('')
  const [detectedSkill, setDetectedSkill] = useState<string | null>(null)
  const [prayerColors, setPrayerColors] = useState<[string, string]>(PERIOD_COLORS['duha'])
  const [prayerPeriod, setPrayerPeriod] = useState<PrayerPeriod>('duha')

  const { start: startAnalyser, stop: stopAnalyser, getAmplitude } = useAudioAnalyser()
  const recognitionRef = useRef<any>(null)
  const routeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const hasRouted = useRef(false)
  const started = useRef(false)
  const voiceDetectRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const speakingHoldTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Prayer colors + period
  useEffect(() => {
    function update() {
      try {
        const schedule = calcPrayerSchedule(DEFAULT_COORDS.latitude, DEFAULT_COORDS.longitude, new Date())
        const period = currentPrayerPeriod(schedule, new Date())
        setPrayerPeriod(period)
        setPrayerColors(PERIOD_COLORS[period])
      } catch {}
    }
    update()
    const id = setInterval(update, 60000)
    return () => clearInterval(id)
  }, [])

  // Start audio analyser (for orb visuals) + speech recognition (for transcription)
  useEffect(() => {
    if (started.current) return
    started.current = true

    async function connectMic() {
      // Try immediately — if no mic, retry once after 2s (USB devices can be slow)
      let ok = await startAnalyser()
      if (!ok) {
        await new Promise(r => setTimeout(r, 2000))
        ok = await startAnalyser()
      }
      setMicConnected(ok)
      if (!ok) {
        setMicReason(await classifyMicFailure())
      } else {
        setMicReason('')
        const id = setInterval(() => {
          const loud = getAmplitude() > 0.02
          if (loud && !micMutedRef.current) {
            // Voice detected — set speaking immediately, hold for 1.5s
            setIsSpeaking(true)
            if (speakingHoldTimer.current) clearTimeout(speakingHoldTimer.current)
            speakingHoldTimer.current = setTimeout(() => setIsSpeaking(false), 1500)
          }
        }, 100)
        voiceDetectRef.current = id
      }
    }

    connectMic()
    startRecognition()

    return () => {
      stopAnalyser()
      stopRecognition()
      if (voiceDetectRef.current) clearInterval(voiceDetectRef.current)
    }
  }, [startAnalyser, stopAnalyser, getAmplitude])

  // Speech retry removed — user has explicit "Retry" button.
  // Auto-retrying on any document click consumed pill interactions.
  const clickRetried = useRef(false)

  function startRecognition() {
    stopRecognition()
    const recognition = createSpeechRecognition()
    if (!recognition) {
      console.warn('[Speech] SpeechRecognition API not available in this browser')
      setMicConnected(false)
      setMicReason('Voice input isn’t supported in this browser — try Chrome or Safari')
      return
    }

    // Voice activity — from Speech Recognition events
    recognition.onspeechstart = () => setIsSpeaking(true)
    recognition.onspeechend = () => setIsSpeaking(false)
    recognition.onsoundend = () => setIsSpeaking(false)

    recognition.onresult = (event: any) => {
      if (hasRouted.current || micMutedRef.current) return

      let allFinal = ''
      let interim = ''

      for (let i = 0; i < event.results.length; i++) {
        const result = event.results[i]
        const transcript = result[0].transcript.trim()
        const confidence = result[0].confidence ?? 0
        if (!transcript || transcript.length < 2) continue

        if (result.isFinal) {
          if (confidence > 0.6 || confidence === 0) allFinal += transcript + ' '
        } else {
          interim += transcript
        }
      }

      setFinalText(allFinal.trim())
      setLiveText(interim)

      const combined = (allFinal + ' ' + interim).trim()
      if (!combined) return

      const skill = detectTrigger(combined)
      if (skill && !hasRouted.current) {
        setDetectedSkill(skill)
        playGlassChime()
        if (routeTimer.current) clearTimeout(routeTimer.current)
        routeTimer.current = setTimeout(() => {
          if (!hasRouted.current) {
            hasRouted.current = true
            stopRecognition()
            onStartSession(skill, combined)
          }
        }, 800)
      }
    }

    let blocked = false

    recognition.onerror = (event: any) => {
      if (['no-speech', 'aborted'].includes(event.error)) return
      console.warn('[Speech] error:', event.error)
      if (['not-allowed', 'service-not-allowed', 'network'].includes(event.error)) {
        blocked = true  // stop the restart loop
        recognitionRef.current = null
      }
    }

    // Only auto-restart if not blocked by permission/network errors
    recognition.onend = () => {
      if (blocked || hasRouted.current || micMutedRef.current) return
      try { recognition.start() } catch {}
    }

    try {
      recognition.start()
      recognitionRef.current = recognition
      setSpeechAvailable(true)
      console.log('[Speech] Recognition started')
    } catch (e) {
      console.warn('[Speech] Failed to start:', e)
    }
  }

  function stopRecognition() {
    try { recognitionRef.current?.stop() } catch {}
    recognitionRef.current = null
    setIsSpeaking(false)
  }

  const handleRetryMic = useCallback(async () => {
    // Retry audio analyser first (mic access)
    const ok = await startAnalyser()
    setMicConnected(ok)
    if (ok) {
      // Also retry speech recognition
      setMicReason('')
      clickRetried.current = false
      startRecognition()
    } else {
      setMicReason(await classifyMicFailure())
    }
  }, [startAnalyser])

  const handleToggleMute = useCallback(() => {
    setMicMuted((prev) => {
      const next = !prev
      micMutedRef.current = next
      if (next) {
        stopRecognition()
        setIsSpeaking(false)
      } else {
        startRecognition()
      }
      return next
    })
  }, [])

  const handleOrbClick = useCallback(() => {
    if (hasRouted.current) return
    hasRouted.current = true
    stopRecognition()
    onStartSession('conversation', (finalText + ' ' + liveText).trim() || undefined)
  }, [finalText, liveText, onStartSession])

  const handlePillSelect = useCallback((mode: PillMode) => {
    if (hasRouted.current) return
    hasRouted.current = true
    playGlassChime()
    stopRecognition()
    onStartSession(mode.skill)
  }, [onStartSession])

  useEffect(() => {
    return () => { if (routeTimer.current) clearTimeout(routeTimer.current) }
  }, [])

  const highlightedPill = detectedSkill
    ? MODES.find((m) => m.skill === detectedSkill)?.id ?? null
    : null

  const displayText = (finalText || liveText)
    ? (finalText + (liveText ? ' ' + liveText : '')).trim()
    : null

  // The orb state: speaking → listening (dramatic), otherwise idle (gentle)
  const orbState = isSpeaking ? 'listening' : 'idle'

  return (
    <div className="flex flex-col h-full relative overflow-hidden">

      {/* Orb as background — drifts organically, voice-reactive */}
      <div
        className="absolute z-0 orb-drift"
        style={{
          inset: '-20%',
          filter: `blur(${isSpeaking ? '40px' : '60px'})`,
          transition: 'filter 1.5s ease-out',
        }}
      >
        <Orb
          fill
          colors={prayerColors}
          state={orbState}
          micConnected={micConnected && !micMuted}
        />
      </div>

      <style>{`
        @keyframes orb-drift {
          0%   { transform: translate(0%, 0%) scale(1.3); }
          20%  { transform: translate(5%, -8%) scale(1.35); }
          40%  { transform: translate(-6%, 4%) scale(1.28); }
          60%  { transform: translate(8%, 6%) scale(1.32); }
          80%  { transform: translate(-4%, -5%) scale(1.36); }
          100% { transform: translate(0%, 0%) scale(1.3); }
        }
        .orb-drift {
          animation: orb-drift 30s ease-in-out infinite;
        }
      `}</style>

      {/* Dark overlay — dims when speaking to let orb glow through */}
      <div
        className="absolute inset-0 z-[1] transition-all duration-500"
        style={{
          backgroundColor: isSpeaking
            ? 'rgba(10,8,6,0.35)'
            : 'rgba(10,8,6,0.55)',
        }}
      />

      {/* Content */}
      <div className="flex-1 flex flex-col items-center justify-center relative z-10">

        {/* Greeting */}
        {!displayText && (
          <p
            className="text-[24px] font-[600] text-white/80 mb-8 text-center animate-[fadeIn_600ms_ease-out]"
            style={{ lineHeight: 1.3 }}
          >
            {getGreeting(prayerPeriod)}
            {(op?.nickname || op?.name) && (
              <>, {op.nickname || op.name?.split(' ')[0]}</>
            )}
          </p>
        )}

        {/* Pills */}
        <div className={`transition-all duration-400 ease-out ${detectedSkill ? 'opacity-50' : 'opacity-100'}`}>
          <OrbitalPills
            onSelect={handlePillSelect}
            hoveredPill={highlightedPill ?? hoveredPill}
            onHoverChange={setHoveredPill}
          />
        </div>

        {/* Live transcription */}
        {displayText && (
          <div className="mt-8 max-w-[500px] text-center animate-[fadeIn_200ms_ease-out]">
            <p className="text-[15px] leading-relaxed">
              {finalText && <span className="text-white/70">{finalText} </span>}
              {liveText && <span className="text-white/40 italic">{liveText}</span>}
            </p>
          </div>
        )}

        {/* Mic status */}
        <MicStatus
          connected={micConnected}
          muted={micMuted}
          speaking={isSpeaking}
          reason={micReason}
          onRetry={handleRetryMic}
          onToggleMute={handleToggleMute}
        />
      </div>

      {/* Recent sessions — pinned to bottom */}
      <RecentSessions />
    </div>
  )
}

// ---------------------------------------------------------------------------
// RecentSessions — subtle list at bottom of launcher
// ---------------------------------------------------------------------------

interface SessionRecord {
  id: string
  title: string
  date: string
  duration_seconds: number
  ended_cleanly?: boolean
  has_summary: boolean
  audio_path?: string
  status?: 'active' | 'paused' | 'ended'
}

// Honest label for the right-hand meta of a recent session. A session that
// never closed has no real duration — say so instead of showing a fabricated
// figure like "2260h 33m".
function sessionMeta(s: SessionRecord): string {
  if (s.status === 'active') return 'active'
  if (s.status === 'paused') return 'paused'
  if (s.ended_cleanly === false) return "didn't end cleanly"
  return formatDuration(s.duration_seconds)
}

function formatDuration(seconds: number): string {
  if (!seconds || seconds < 0) return ''
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  if (m < 60) return `${m}m`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

function formatTimeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days === 1) return 'yesterday'
  if (days < 7) return `${days}d ago`
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function RecentSessions() {
  const navigate = useNavigate()
  const [sessions, setSessions] = useState<SessionRecord[]>([])
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetch('/companion/sessions?limit=5')
      .then(r => { if (!r.ok) throw new Error(); return r.json() })
      .then(data => {
        if (!cancelled) {
          const all = Array.isArray(data) ? data : []
          // Meaningful = has real content. Ended sessions qualify by duration;
          // never-closed / ongoing ones have no real duration, so keep them by
          // state instead (they render with an honest label, not a fake time).
          const hasContent = (s: SessionRecord) =>
            s.duration_seconds > 10
            || s.ended_cleanly === false
            || s.status === 'active'
            || s.status === 'paused'
          const meaningful = all.filter((s: SessionRecord) =>
            s.title && !s.title.startsWith('Untitled') && hasContent(s)
          )
          // Fall back to any session with content if no titled ones
          const candidates = meaningful.length > 0
            ? meaningful
            : all.filter(hasContent)
          setSessions(candidates.slice(0, 5))
          setLoaded(true)
        }
      })
      .catch(() => { if (!cancelled) setLoaded(true) })
    return () => { cancelled = true }
  }, [])

  if (!loaded || sessions.length === 0) return null

  return (
    <div className="relative z-10 shrink-0 pb-5 px-6 animate-[fadeIn_400ms_ease-out_200ms_both]">
      <div className="max-w-[420px] mx-auto">
        <div className="flex items-center gap-2 mb-2 px-1">
          <Clock className="w-3 h-3 text-white/25" />
          <span className="text-[10px] font-[510] uppercase tracking-[0.06em] text-white/25">
            Recent
          </span>
          <span className="flex-1" />
          <button
            onClick={() => navigate('/sessions')}
            className="text-[10px] font-[450] text-white/20 hover:text-accent transition-colors cursor-pointer"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            See all
          </button>
        </div>
        <div className="space-y-px">
          {sessions.map(s => (
            <button
              key={s.id}
              onClick={() => navigate(s.status === 'active' || s.status === 'paused' ? `/companion/session/${s.id}` : `/sessions/${s.id}`)}
              className="
                w-full flex items-center gap-3 px-3 py-2
                rounded-[8px] text-left cursor-pointer
                hover:bg-white/[0.04] transition-colors group
              "
              style={{ transitionDuration: 'var(--duration-instant)' }}
            >
              <span className="flex-1 min-w-0 text-[12px] font-[450] text-white/35 truncate group-hover:text-white/55 transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
                {s.title || 'Untitled'}
              </span>
              <span className="text-[10px] text-white/20 shrink-0 tabular-nums group-hover:text-white/35 transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
                {sessionMeta(s)}
              </span>
              <span className="text-[10px] text-white/20 shrink-0 tabular-nums group-hover:text-white/35 transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
                {formatTimeAgo(s.date)}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// MicStatus
// ---------------------------------------------------------------------------

function MicStatus({
  connected, muted, speaking, reason, onRetry, onToggleMute,
}: {
  connected: boolean; muted: boolean; speaking: boolean; reason?: string
  onRetry: () => void; onToggleMute: () => void
}) {
  if (!connected) {
    return (
      <div className="mt-6 flex flex-col items-center gap-1.5 animate-[fadeIn_300ms_ease-out]">
        <div className="flex items-center gap-2">
          <MicOff className="w-3.5 h-3.5 text-red-400/60" />
          <span className="text-[11px] text-text-quaternary tracking-wide">Mic unavailable</span>
          <button onClick={onRetry} className="text-[11px] text-accent/60 hover:text-accent tracking-wide cursor-pointer transition-colors">
            Retry
          </button>
        </div>
        {reason && (
          <span className="text-[10px] text-text-quaternary/70 tracking-wide text-center max-w-[280px] leading-snug">
            {reason}
          </span>
        )}
      </div>
    )
  }

  return (
    <div className="mt-6 flex items-center justify-center gap-2.5">
      <button
        onClick={onToggleMute}
        className={`
          flex items-center justify-center w-7 h-7 rounded-full border cursor-pointer transition-all duration-200
          ${muted
            ? 'bg-[rgba(255,80,80,0.08)] border-[rgba(255,80,80,0.2)] text-red-400/70 hover:text-red-400'
            : 'bg-transparent border-transparent text-text-quaternary/50 hover:text-text-tertiary hover:bg-[rgba(255,245,235,0.04)]'
          }
        `}
        title={muted ? 'Unmute' : 'Mute'}
      >
        {muted ? <MicOff className="w-3 h-3" /> : <Mic className="w-3 h-3" />}
      </button>
      {muted ? (
        <span className="text-[11px] text-text-quaternary/50 tracking-wide">Muted</span>
      ) : (
        <span className="relative flex h-2 w-2">
          {speaking && <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent/40" />}
          <span className={`relative inline-flex rounded-full h-2 w-2 transition-all duration-300 ${speaking ? 'bg-accent shadow-[0_0_6px_rgba(217,115,13,0.5)]' : 'bg-text-quaternary/40'}`} />
        </span>
      )}
    </div>
  )
}
