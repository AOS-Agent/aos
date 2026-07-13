import { useState, useCallback, useRef, useEffect, useMemo } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import {
  Mic, Send, MicOff, Minus, Maximize2, Check, X,
  ListTodo, Search, FileText, Zap,
  Users, Activity, BookOpen, CalendarDays,
} from 'lucide-react'
import { format } from 'date-fns'
import { AgentOrb } from './AgentOrb'
import { useAssist } from '@/hooks/useAssist'
import { useQuickVoice } from '@/hooks/useQuickVoice'
import { usePageActions } from '@/hooks/usePageActions'

// ---------------------------------------------------------------------------
// FloatingAgent — voice-first quick-assist on every page.
//
// Orb: warm organic blob. Tap = open panel. Long-press = voice command.
// Panel: glass card with action-aware suggestions + compact action log.
// Wired to POST /api/assist (Claude Haiku) for instant page manipulation.
// ---------------------------------------------------------------------------

type AgentPhase = 'orb' | 'panel'

interface ActionEntry {
  id: string
  input: string
  spoken: string
  success: boolean
  ts: number
}

interface Suggestion {
  icon: React.ReactNode
  label: string
  prompt: string
}

// ── Page context — granular, reads sub-routes and query params ──

function getPageContext(pathname: string, searchParams: URLSearchParams): { label: string; detail: string | null } | null {
  if (pathname === '/work' || pathname.startsWith('/work')) {
    const tab = searchParams.get('tab')
    const tabLabels: Record<string, string> = { today: 'Today', tasks: 'Tasks', projects: 'Projects', goals: 'Goals' }
    return { label: 'Work', detail: tab ? tabLabels[tab] ?? tab : 'Today' }
  }
  if (pathname.startsWith('/vault')) {
    if (pathname.includes('/knowledge')) return { label: 'Vault', detail: 'Knowledge' }
    if (pathname.includes('/logs')) return { label: 'Vault', detail: 'Logs' }
    return { label: 'Vault', detail: null }
  }
  if (pathname === '/timeline' || pathname.startsWith('/timeline')) {
    const rest = pathname.replace('/timeline', '').replace(/^\//, '')
    if (!rest) return { label: 'Timeline', detail: 'Today' }
    const parts = rest.split('/')
    const viewLabels: Record<string, string> = { day: 'Day', week: 'Week', month: 'Month', year: 'Year' }
    if (parts.length === 2 && viewLabels[parts[0]]) {
      const dateStr = formatDateLabel(parts[1])
      return { label: 'Timeline', detail: `${viewLabels[parts[0]]}${dateStr ? ' · ' + dateStr : ''}` }
    }
    if (parts.length === 1) {
      const dateStr = formatDateLabel(parts[0])
      return { label: 'Timeline', detail: dateStr ?? parts[0] }
    }
    return { label: 'Timeline', detail: null }
  }
  if (pathname === '/settings') {
    const section = searchParams.get('section')
    if (section) return { label: 'Settings', detail: section.charAt(0).toUpperCase() + section.slice(1) }
    return { label: 'Settings', detail: null }
  }
  if (pathname === '/people') return { label: 'People', detail: null }
  if (pathname === '/system') return { label: 'System', detail: null }
  if (pathname === '/agents') return { label: 'Agents', detail: null }
  if (pathname === '/chat') return { label: 'Chat', detail: null }
  return null
}

function formatDateLabel(dateStr: string): string | null {
  try {
    const d = new Date(dateStr + 'T00:00:00')
    if (isNaN(d.getTime())) return null
    return format(d, 'MMM d')
  } catch { return null }
}

// ── Context-aware suggestions (fallback for pages without registered actions) ──

function getSuggestions(pathname: string): Suggestion[] {
  if (pathname === '/work' || pathname.startsWith('/work')) {
    return [
      { icon: <ListTodo className="w-3.5 h-3.5" />, label: 'Switch to Tasks tab', prompt: 'switch to the tasks tab' },
      { icon: <Zap className="w-3.5 h-3.5" />, label: 'Add a task', prompt: 'create a new task called ' },
      { icon: <FileText className="w-3.5 h-3.5" />, label: 'Show today view', prompt: 'show today view' },
    ]
  }
  if (pathname.startsWith('/vault')) {
    return [
      { icon: <Search className="w-3.5 h-3.5" />, label: 'Search the vault', prompt: 'search for ' },
      { icon: <BookOpen className="w-3.5 h-3.5" />, label: 'Show knowledge', prompt: 'show knowledge section' },
    ]
  }
  if (pathname === '/people' || pathname.startsWith('/people')) {
    return [
      { icon: <Users className="w-3.5 h-3.5" />, label: 'Find someone', prompt: 'find ' },
      { icon: <Search className="w-3.5 h-3.5" />, label: 'Show org chart', prompt: 'switch to org chart view' },
    ]
  }
  if (pathname === '/system' || pathname.startsWith('/system')) {
    return [
      { icon: <Activity className="w-3.5 h-3.5" />, label: 'System health', prompt: 'what is the system health?' },
    ]
  }
  if (pathname === '/timeline' || pathname.startsWith('/timeline')) {
    return [
      { icon: <CalendarDays className="w-3.5 h-3.5" />, label: 'Show week view', prompt: 'switch to week view' },
    ]
  }
  return [
    { icon: <ListTodo className="w-3.5 h-3.5" />, label: 'Go to Work', prompt: 'go to work' },
    { icon: <Search className="w-3.5 h-3.5" />, label: 'Go to Vault', prompt: 'go to vault' },
    { icon: <CalendarDays className="w-3.5 h-3.5" />, label: 'Go to Timeline', prompt: 'go to timeline' },
  ]
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function FloatingAgent() {
  const location = useLocation()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [phase, setPhase] = useState<AgentPhase>('orb')
  const [entries, setEntries] = useState<ActionEntry[]>([])
  const [input, setInput] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const longPressRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [orbConfirm, setOrbConfirm] = useState<string | null>(null)

  const { assist, isPending } = useAssist()
  const actions = usePageActions()

  // Voice — single-shot recognition for quick commands
  const handleVoiceResult = useCallback(async (text: string) => {
    const result = await assist(text)
    const entry: ActionEntry = {
      id: crypto.randomUUID(),
      input: text,
      spoken: result?.response.spoken ?? 'No response',
      success: result?.execution.success ?? false,
      ts: Date.now(),
    }
    setEntries(prev => [...prev, entry])

    // If panel is closed, show brief orb confirmation
    if (phase === 'orb') {
      setOrbConfirm(entry.spoken)
      setTimeout(() => setOrbConfirm(null), 2500)
    }
  }, [assist, phase])

  const voice = useQuickVoice(handleVoiceResult)

  const suggestions = useMemo(() => getSuggestions(location.pathname), [location.pathname])
  const pageCtx = useMemo(() => getPageContext(location.pathname, searchParams), [location.pathname, searchParams])

  // Count page-specific (non-global) actions
  const pageActionCount = useMemo(() => actions.filter(a => !a.id.startsWith('nav.') && !a.id.startsWith('ui.')).length, [actions])

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [entries, isPending])

  useEffect(() => {
    if (phase === 'panel') setTimeout(() => inputRef.current?.focus(), 220)
  }, [phase])

  useEffect(() => {
    if (phase !== 'panel') return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setPhase('orb') }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [phase])

  const togglePanel = useCallback(() => setPhase(p => p === 'orb' ? 'panel' : 'orb'), [])

  // ── Send text to assist API ──
  const sendText = useCallback(async (text: string) => {
    if (!text.trim()) return
    setInput('')
    if (inputRef.current) inputRef.current.style.height = 'auto'

    const result = await assist(text.trim())
    setEntries(prev => [...prev, {
      id: crypto.randomUUID(),
      input: text.trim(),
      spoken: result?.response.spoken ?? 'Something went wrong.',
      success: result?.execution.success ?? false,
      ts: Date.now(),
    }])
  }, [assist])

  const handleSend = useCallback(() => sendText(input), [input, sendText])
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }, [handleSend])

  const handleSuggestion = useCallback((s: Suggestion) => {
    if (s.prompt.endsWith(' ')) { setInput(s.prompt); inputRef.current?.focus() }
    else sendText(s.prompt)
  }, [sendText])

  const handleClear = useCallback(() => { setEntries([]); setInput('') }, [])
  const handleFullscreen = useCallback(() => { setPhase('orb'); navigate('/chat') }, [navigate])

  // ── Long-press orb = voice command without opening panel ──
  const handleOrbPointerDown = useCallback(() => {
    longPressRef.current = setTimeout(() => {
      longPressRef.current = null
      if (voice.supported) voice.start()
    }, 400)
  }, [voice])

  const handleOrbPointerUp = useCallback(() => {
    if (longPressRef.current) {
      clearTimeout(longPressRef.current)
      longPressRef.current = null
      togglePanel()
    }
  }, [togglePanel])

  const handleOrbPointerLeave = useCallback(() => {
    if (longPressRef.current) {
      clearTimeout(longPressRef.current)
      longPressRef.current = null
    }
  }, [])

  const resizeTextarea = useCallback(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 120) + 'px'
  }, [])

  const hasEntries = entries.length > 0 || isPending

  // Don't show on Companion page
  // Hide on home and companion session pages
  if (location.pathname === '/' || location.pathname.startsWith('/companion/session')) return null

  const contextLabel = pageCtx
    ? pageCtx.detail
      ? `${pageCtx.label} · ${pageCtx.detail}`
      : pageCtx.label
    : null

  const agentState = voice.listening ? 'listening' : isPending ? 'thinking' : null

  return (
    <>
      {/* ── Orb ── */}
      <div className={`fixed bottom-6 right-6 z-[280] flex flex-col items-end gap-2 ${phase === 'panel' ? 'pointer-events-none' : ''}`}>
        {/* Orb confirmation bubble */}
        {orbConfirm && (
          <div
            className="px-3 py-1.5 rounded-[10px] text-[11px] font-[470] text-text-secondary max-w-[200px] pointer-events-none animate-[fade-up_220ms_ease-out]"
            style={{
              background: 'var(--glass-bg)',
              backdropFilter: 'blur(16px)',
              WebkitBackdropFilter: 'blur(16px)',
              border: '1px solid var(--glass-border)',
            }}
          >
            {orbConfirm}
          </div>
        )}

        {/* Voice listening indicator */}
        {voice.listening && phase === 'orb' && (
          <div
            className="px-3 py-1.5 rounded-[10px] text-[11px] font-[470] text-accent pointer-events-none"
            style={{
              background: 'var(--glass-bg)',
              backdropFilter: 'blur(16px)',
              WebkitBackdropFilter: 'blur(16px)',
              border: '1px solid rgba(217,115,13,0.2)',
            }}
          >
            Listening...
          </div>
        )}

        <button
          type="button"
          onPointerDown={handleOrbPointerDown}
          onPointerUp={handleOrbPointerUp}
          onPointerLeave={handleOrbPointerLeave}
          aria-label={voice.listening ? 'Listening for voice command' : 'Open assistant'}
          className={`
            w-12 h-12 rounded-full overflow-hidden
            cursor-pointer select-none
            transition-all duration-[220ms] ease-[cubic-bezier(0.25,0.46,0.45,0.94)]
            hover:scale-[1.15]
            ${phase === 'panel' ? 'scale-0 opacity-0' : 'scale-100 opacity-100'}
          `}
        >
          <AgentOrb
            colors={["#E8842A", "#D9730D"]}
            agentState={agentState}
            seed={42}
            className="absolute inset-0"
          />
        </button>
      </div>

      {/* ── Panel ── */}
      <div
        className={`
          fixed bottom-6 right-6 z-[280]
          w-[360px] max-w-[calc(100vw-48px)]
          flex flex-col
          rounded-[16px]
          overflow-hidden
          transition-all duration-[220ms] ease-[cubic-bezier(0.25,0.46,0.45,0.94)]
          ${phase === 'panel'
            ? 'opacity-100 scale-100 translate-y-0 pointer-events-auto'
            : 'opacity-0 scale-[0.92] translate-y-3 pointer-events-none'
          }
        `}
        style={{
          height: 'min(420px, calc(100dvh - 80px))',
          background: 'var(--glass-bg)',
          backdropFilter: 'blur(24px)',
          WebkitBackdropFilter: 'blur(24px)',
          border: '1px solid var(--glass-border)',
          boxShadow: '0 16px 56px rgba(0,0,0,0.50), 0 0 0 1px rgba(255,245,235,0.03)',
        }}
      >
        {/* ── Header ── */}
        <div className="flex items-center justify-between px-3 h-9 shrink-0 border-b border-[rgba(255,245,235,0.05)]">
          <div className="flex items-center gap-1.5">
            <div className={`w-[6px] h-[6px] rounded-full ${voice.listening ? 'bg-accent animate-pulse' : isPending ? 'bg-accent animate-pulse' : 'bg-text-quaternary'}`} />
            <span className="text-[11px] font-[530] text-text-secondary tracking-[-0.01em]">
              Quick Assist
            </span>
            {contextLabel && (
              <>
                <span className="text-[10px] text-text-quaternary">·</span>
                <span className="text-[10px] font-[450] text-text-quaternary">{contextLabel}</span>
              </>
            )}
            {pageActionCount > 0 && (
              <span className="text-[9px] font-[500] text-text-quaternary bg-[rgba(255,245,235,0.04)] rounded-full px-1.5 py-0.5">
                {pageActionCount} actions
              </span>
            )}
          </div>
          <div className="flex items-center gap-0.5">
            <button type="button" onClick={handleFullscreen} className="w-6 h-6 rounded-[4px] flex items-center justify-center text-text-quaternary hover:text-text-tertiary hover:bg-hover transition-colors cursor-pointer" style={{ transitionDuration: 'var(--duration-instant)' }} title="Full chat">
              <Maximize2 className="w-3 h-3" />
            </button>
            <button type="button" onClick={togglePanel} className="w-6 h-6 rounded-[4px] flex items-center justify-center text-text-quaternary hover:text-text-tertiary hover:bg-hover transition-colors cursor-pointer" style={{ transitionDuration: 'var(--duration-instant)' }} title="Minimize">
              <Minus className="w-3 h-3" />
            </button>
          </div>
        </div>

        {/* ── Content ── */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 min-h-0">
          {!hasEntries ? (
            <div className="flex flex-col pt-5 pb-2">
              <p className="text-[13px] font-[520] text-text tracking-[-0.01em] mb-0.5">
                {voice.supported ? 'Speak or type a command' : 'Type a command'}
              </p>
              <p className="text-[11px] text-text-quaternary leading-[1.4] mb-4">
                {pageCtx
                  ? `You're on ${contextLabel}. Try:`
                  : 'Navigate, search, or take action:'
                }
              </p>
              <div className="space-y-1">
                {suggestions.map(s => (
                  <button
                    key={s.label}
                    type="button"
                    onClick={() => handleSuggestion(s)}
                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-[8px] text-left cursor-pointer border border-[rgba(255,245,235,0.06)] bg-[rgba(255,245,235,0.02)] hover:bg-[rgba(255,245,235,0.05)] hover:border-[rgba(255,245,235,0.10)] transition-all group"
                    style={{ transitionDuration: 'var(--duration-instant)' }}
                  >
                    <span className="text-text-quaternary group-hover:text-text-tertiary transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>{s.icon}</span>
                    <span className="text-[12px] font-[450] text-text-tertiary group-hover:text-text-secondary transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>{s.label}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="space-y-1.5 py-3">
              {entries.map(entry => (
                <div key={entry.id} className="flex flex-col gap-0.5">
                  <div className="flex justify-end">
                    <div className="max-w-[85%] px-3 py-1.5 rounded-[10px] text-[11px] font-[440] text-text-secondary bg-[rgba(255,245,235,0.06)] border border-[rgba(255,245,235,0.04)]">
                      {entry.input}
                    </div>
                  </div>
                  <div className="flex items-start gap-1.5">
                    <span className={`mt-0.5 shrink-0 ${entry.success ? 'text-green' : 'text-text-quaternary'}`}>
                      {entry.success ? <Check className="w-3 h-3" /> : <X className="w-3 h-3" />}
                    </span>
                    <span className="text-[12px] font-serif font-[440] text-text-secondary leading-[1.5]">
                      {entry.spoken}
                    </span>
                  </div>
                </div>
              ))}
              {isPending && (
                <div className="flex items-center gap-1.5 px-1 py-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-text-quaternary" style={{ animation: 'agent-dot 1.4s ease-in-out infinite' }} />
                  <span className="w-1.5 h-1.5 rounded-full bg-text-quaternary" style={{ animation: 'agent-dot 1.4s ease-in-out 0.2s infinite' }} />
                  <span className="w-1.5 h-1.5 rounded-full bg-text-quaternary" style={{ animation: 'agent-dot 1.4s ease-in-out 0.4s infinite' }} />
                </div>
              )}
              {entries.length > 0 && !isPending && (
                <button
                  type="button"
                  onClick={handleClear}
                  className="text-[10px] font-[450] text-text-quaternary hover:text-text-tertiary transition-colors cursor-pointer pt-1"
                  style={{ transitionDuration: 'var(--duration-instant)' }}
                >
                  Clear history
                </button>
              )}
            </div>
          )}
        </div>

        {/* ��─ Input bar ── */}
        <div className="shrink-0 px-2.5 pb-2.5 pt-0.5">
          <div
            className="rounded-[10px] px-2.5 pt-2 pb-1.5 border border-[rgba(255,245,235,0.07)] bg-[rgba(21,18,16,0.45)] focus-within:border-[rgba(217,115,13,0.18)] transition-colors"
            style={{ transitionDuration: 'var(--duration-fast)' }}
          >
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => { setInput(e.target.value); resizeTextarea() }}
              onKeyDown={handleKeyDown}
              placeholder={voice.listening ? 'Listening...' : 'Type a command...'}
              rows={1}
              disabled={voice.listening}
              className="w-full bg-transparent text-[12px] font-[440] text-text placeholder:text-text-quaternary outline-none resize-none leading-[1.5] max-h-[80px] disabled:opacity-50"
            />
            <div className="flex items-center justify-end -mx-0.5">
              <div className="flex items-center gap-0.5">
                {voice.supported && (
                  <button
                    type="button"
                    onClick={voice.toggle}
                    className={`w-7 h-7 rounded-[6px] flex items-center justify-center shrink-0 transition-all cursor-pointer ${voice.listening ? 'bg-accent/15 text-accent' : 'text-text-quaternary hover:text-text-tertiary hover:bg-hover'}`}
                    style={{ transitionDuration: 'var(--duration-instant)' }}
                    title={voice.listening ? 'Stop listening' : 'Voice command'}
                  >
                    {voice.listening ? <Mic className="w-3.5 h-3.5" /> : <MicOff className="w-3.5 h-3.5" />}
                  </button>
                )}
                <button
                  type="button"
                  onClick={handleSend}
                  disabled={!input.trim() || isPending}
                  className={`w-7 h-7 rounded-full flex items-center justify-center shrink-0 transition-all ${input.trim() && !isPending ? 'bg-accent text-bg cursor-pointer hover:bg-accent-hover' : 'text-text-quaternary cursor-default opacity-20'}`}
                  style={{ transitionDuration: 'var(--duration-instant)' }}
                >
                  <Send className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <style>{`
        @keyframes agent-dot {
          0%, 80%, 100% { transform: scale(0.6); opacity: 0.3; }
          40% { transform: scale(1); opacity: 1; }
        }
        @keyframes fade-up {
          from { opacity: 0; transform: translateY(4px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </>
  )
}
