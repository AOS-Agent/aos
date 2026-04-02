import { useState, useCallback, useRef } from 'react'
import { useCompanion } from '@/hooks/useCompanion'
import { useCompanionStore } from '@/store/companion'
import { useApprovals } from '@/hooks/useApprovals'
import { useVoiceCapture } from '@/hooks/useVoiceCapture'
// Aurora removed — the orb IS the background now
import { ContextBar } from '@/components/companion/ContextBar'
import { SessionLauncher } from '@/components/companion/SessionLauncher'
import { SessionSetup, type SessionConfig } from '@/components/companion/SessionSetup'
import { SessionHeader } from '@/components/companion/SessionHeader'
import { TranscriptPanel } from '@/components/companion/TranscriptPanel'
import { WorkspacePanel } from '@/components/companion/WorkspacePanel'
import { ThinkingStrip } from '@/components/companion/ThinkingStrip'
import { UnifiedInput } from '@/components/companion/UnifiedInput'
import { SessionEndReview } from '@/components/companion/SessionEndReview'
import type { SessionState } from '@/store/companion'

// ---------------------------------------------------------------------------
// Companion — pipeline layout page.
//
// Four visual states, all on the persistent aurora background:
//   1. endedSession  → SessionEndReview
//   2. idle          → SessionLauncher (greeting + chips)
//   3. setupMode     → SessionSetup (glass card for intent + context)
//   4. active session → Glass panel workspace (transcript + workspace)
//
// The aurora canvas NEVER unmounts. It flows behind all states.
// ---------------------------------------------------------------------------

type TransitionPhase = 'idle' | 'setup' | 'workspace' | 'review'

export default function Companion() {
  // Connect SSE stream
  useCompanion()

  const session = useCompanionStore((s) => s.session)
  const setSession = useCompanionStore((s) => s.setSession)
  const addPausedSession = useCompanionStore((s) => s.addPausedSession)
  const transcriptCollapsed = useCompanionStore((s) => s.transcriptCollapsed)
  const noteGroups = useCompanionStore((s) => s.noteGroups)
  const approvals = useCompanionStore((s) => s.approvals)
  const clearSegments = useCompanionStore((s) => s.clearSegments)
  const clearNoteGroups = useCompanionStore((s) => s.clearNoteGroups)
  const clearApprovals = useCompanionStore((s) => s.clearApprovals)
  const clearCards = useCompanionStore((s) => s.clearCards)
  const clearStream = useCompanionStore((s) => s.clearStream)
  const clearContextCards = useCompanionStore((s) => s.clearContextCards)
  const clearSessionData = useCompanionStore((s) => s.clearSessionData)
  const { approve, startApproval, undo, dismiss, edit } = useApprovals()
  const { activate: activateMic } = useVoiceCapture()
  const micAutoStarted = useRef(false)

  // Setup mode — the glass card between idle and active
  const [setupMode, setSetupMode] = useState(false)
  const [setupInitialText, setSetupInitialText] = useState('')
  const [setupSkill, setSetupSkill] = useState<string | null>(null)

  // Session end review state
  const [endedSession, setEndedSession] = useState<SessionState | null>(null)
  const [endedNoteGroups, setEndedNoteGroups] = useState(noteGroups)
  const [endedApprovals, setEndedApprovals] = useState(approvals)

  // Transition animation tracking
  const [phase, setPhase] = useState<TransitionPhase>('idle')
  const [transitioning, setTransitioning] = useState(false)

  // Enter setup mode from idle (pill click or orb click)
  const handleEnterSetup = useCallback((type: string, text?: string) => {
    setSetupInitialText(text ?? '')
    setSetupSkill(type)
    setTransitioning(true)
    setPhase('setup')
    setTimeout(() => {
      setSetupMode(true)
      setTransitioning(false)
    }, 200)
  }, [])

  // Start a session from the setup card
  const handleSetupStart = useCallback(
    (config: SessionConfig) => {
      // Clear previous session data
      clearSessionData()
      micAutoStarted.current = false

      const sessionId = crypto.randomUUID()

      const newSession = {
        id: sessionId,
        title: getSessionTitle(config.skill, config.intent),
        type: config.type,
        skill: config.skill,
        startedAt: new Date().toISOString(),
        status: 'active' as const,
        stats: { processed: 0, total: 0, approved: 0 },
      }

      // Transition: setup → workspace
      setTransitioning(true)
      setTimeout(() => {
        setSetupMode(false)
        setSession(newSession)
        setPhase('workspace')
        setTransitioning(false)
      }, 200)

      // Notify backend
      fetch('/companion/session/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: config.type,
          skill: newSession.skill,
          title: newSession.title,
          people: config.people,
          context: config.context,
        }),
      }).catch(() => {})

      // Send intent as first input
      if (config.intent) {
        fetch('/companion/input', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: config.intent, source: 'text' }),
        }).catch(() => {})
      }

      // Auto-start mic for meetings
      if (config.skill === 'meeting' && !micAutoStarted.current) {
        micAutoStarted.current = true
        setTimeout(() => {
          activateMic().catch(() => {})
        }, 500)
      }
    },
    [setSession, clearSessionData, activateMic],
  )

  // Cancel setup → back to idle
  const handleSetupCancel = useCallback(() => {
    setTransitioning(true)
    setTimeout(() => {
      setSetupMode(false)
      setPhase('idle')
      setTransitioning(false)
    }, 200)
  }, [])

  // Legacy: direct session start (bypasses setup for quick actions)
  const handleStartSession = useCallback(
    (type: string, text?: string) => {
      // Route through setup mode for richer intent
      handleEnterSetup(type, text)
    },
    [handleEnterSetup],
  )

  // Pause session
  const handlePause = useCallback(() => {
    if (!session) return

    addPausedSession({
      id: session.id,
      title: session.title,
      type: session.type,
      pausedAt: new Date().toISOString(),
    })

    setSession(null)
    setPhase('idle')

    fetch('/companion/session/pause', { method: 'POST' }).catch(() => {})
  }, [session, setSession, addPausedSession])

  // Stop session — show end review
  const handleStop = useCallback(() => {
    if (!session) return

    setEndedSession({ ...session })
    setEndedNoteGroups([...noteGroups])
    setEndedApprovals([...approvals])

    setSession(null)
    setPhase('review')

    fetch('/companion/session/stop', { method: 'POST' }).catch(() => {})
  }, [session, setSession, noteGroups, approvals])

  // Save and close from end review
  const handleSaveAndClose = useCallback(
    (summary: string, saveToVault: boolean) => {
      if (endedSession) {
        fetch(`/companion/session/${endedSession.id}/save`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ summary, save_to_vault: saveToVault }),
        }).catch(() => {})
      }

      setEndedSession(null)
      setPhase('idle')
      clearSegments()
      clearNoteGroups()
      clearApprovals()
      clearCards()
      clearStream()
      clearContextCards()
    },
    [endedSession, clearSegments, clearNoteGroups, clearApprovals, clearCards, clearStream, clearContextCards],
  )

  // Discard from end review
  const handleDiscardReview = useCallback(() => {
    setEndedSession(null)
    setPhase('idle')
    clearSegments()
    clearNoteGroups()
    clearApprovals()
    clearCards()
    clearStream()
    clearContextCards()
  }, [clearSegments, clearNoteGroups, clearApprovals, clearCards, clearStream, clearContextCards])

  // Send text
  const handleSendText = useCallback((_text: string) => {}, [])

  // Approval handlers
  const handleApprove = useCallback((id: string) => { approve(id) }, [approve])
  const handleStartApproval = useCallback((id: string) => { startApproval(id) }, [startApproval])
  const handleUndo = useCallback((id: string) => { undo(id) }, [undo])
  const handleDismiss = useCallback((id: string) => { dismiss(id) }, [dismiss])
  const handleEdit = useCallback((id: string) => { edit(id, {}) }, [edit])

  // Determine current view
  const showEndReview = !session && endedSession
  const showSetup = !session && !endedSession && setupMode
  const showIdle = !session && !endedSession && !setupMode
  const showWorkspace = !!session

  return (
    <div className="flex flex-col h-full relative overflow-hidden bg-[#0A0806]">
      {/* Context bar — top right corner */}
      <ContextBar />

      {/* Content layer — on top of aurora */}
      <div className="relative z-10 flex flex-col h-full">

        {/* ---- End review ---- */}
        {showEndReview && (
          <div className="flex flex-col h-full session-setup-enter">
            <SessionEndReview
              session={endedSession!}
              noteGroups={endedNoteGroups}
              approvals={endedApprovals}
              onSaveAndClose={handleSaveAndClose}
              onDiscard={handleDiscardReview}
            />
          </div>
        )}

        {/* ---- Idle: launcher ---- */}
        {showIdle && (
          <div className={`flex flex-col h-full ${transitioning && phase === 'setup' ? 'companion-fade-out' : 'companion-fade-in'}`} style={{ opacity: 0 }}>
            <SessionLauncher onStartSession={handleStartSession} />
          </div>
        )}

        {/* ---- Setup: glass card ---- */}
        {showSetup && (
          <div className={`flex flex-col h-full ${transitioning ? 'companion-fade-out' : 'companion-fade-in'}`}>
            <SessionSetup
              initialText={setupInitialText}
              initialSkill={setupSkill}
              onStart={handleSetupStart}
              onCancel={handleSetupCancel}
            />
          </div>
        )}

        {/* ---- Active workspace: glass panels ---- */}
        {showWorkspace && (
          <div className="flex flex-col h-full relative">
            {/* Session header — floating glass pill */}
            <SessionHeader
              session={session!}
              onPause={handlePause}
              onStop={handleStop}
            />

            {/* Two glass panel layout */}
            <div className="flex-1 flex gap-3 p-3 pt-14 min-h-0">
              {/* Transcript — left glass card */}
              <div
                className={`
                  hidden md:flex shrink-0
                  ${transcriptCollapsed ? 'w-12' : 'w-[38%] min-w-[280px] max-w-[480px]'}
                  rounded-[14px]
                  bg-[rgba(21,18,16,0.50)]
                  backdrop-blur-[20px]
                  border border-[rgba(255,245,235,0.08)]
                  shadow-[0_4px_24px_rgba(0,0,0,0.3)]
                  overflow-hidden
                  flex-col
                  transition-all duration-[220ms] ease-[cubic-bezier(0.25,0.46,0.45,0.94)]
                  glass-panel-enter
                `}
              >
                <TranscriptPanel />
              </div>

              {/* Workspace — right glass card */}
              <div
                className="
                  flex-1 min-w-0
                  rounded-[14px]
                  bg-[rgba(21,18,16,0.50)]
                  backdrop-blur-[20px]
                  border border-[rgba(255,245,235,0.08)]
                  shadow-[0_4px_24px_rgba(0,0,0,0.3)]
                  overflow-hidden
                  flex flex-col
                  glass-panel-enter glass-panel-delay
                "
              >
                <WorkspacePanel
                  onApprove={handleApprove}
                  onStartApproval={handleStartApproval}
                  onUndo={handleUndo}
                  onDismiss={handleDismiss}
                  onEdit={handleEdit}
                />
              </div>
            </div>

            {/* Thinking strip */}
            <ThinkingStrip />

            {/* Unified input — glass floating bar */}
            <div className="px-3 pb-3">
              <UnifiedInput onSendText={handleSendText} />
            </div>

            {/* Mobile transcript bottom sheet */}
            <MobileTranscriptSheet />
          </div>
        )}
      </div>

      {/* Transition + glass panel animations */}
      <style>{`
        .companion-fade-in {
          animation: companion-in 300ms ease-out forwards;
        }
        .companion-fade-out {
          animation: companion-out 200ms ease-in forwards;
        }
        @keyframes companion-in {
          from { opacity: 0; transform: translateY(12px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes companion-out {
          from { opacity: 1; transform: translateY(0); }
          to   { opacity: 0; transform: translateY(-8px); }
        }

        .session-setup-enter {
          animation: setup-in 300ms ease-out forwards;
        }
        @keyframes setup-in {
          from { opacity: 0; transform: translateY(20px) scale(0.98); }
          to   { opacity: 1; transform: translateY(0) scale(1); }
        }

        .glass-panel-enter {
          animation: panel-in 300ms ease-out forwards;
        }
        .glass-panel-delay {
          animation-delay: 100ms;
          opacity: 0;
        }
        @keyframes panel-in {
          from { opacity: 0; transform: translateY(20px) scale(0.97); }
          to   { opacity: 1; transform: translateY(0) scale(1); }
        }
      `}</style>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Mobile transcript bottom sheet
// ---------------------------------------------------------------------------

function MobileTranscriptSheet() {
  const segments = useCompanionStore((s) => s.segments)
  const [expanded, setExpanded] = useState(false)

  if (segments.length === 0) return null

  return (
    <div className="md:hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="
          fixed bottom-[120px] left-3
          h-8 px-3 rounded-full
          bg-bg-tertiary text-text-secondary
          text-[11px] font-medium
          border border-border-secondary
          shadow-medium
          z-10
        "
      >
        Transcript ({segments.length})
      </button>

      {expanded && (
        <div className="
          fixed inset-0 z-[var(--z-overlay)]
          bg-bg/95 backdrop-blur-sm
          flex flex-col
          animate-[mobile-sheet_200ms_ease-out]
        ">
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <span className="type-heading text-text">Transcript</span>
            <button
              onClick={() => setExpanded(false)}
              className="type-label text-accent"
            >
              Close
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-4 py-2">
            {segments.map((seg) => {
              const speaker = normalizeMobileSpeaker(seg.speaker)
              const isOperator = speaker === 'Hisham'
              return (
                <div key={seg.id} className="py-1">
                  <span className={`type-label ${isOperator ? 'text-accent' : 'text-text-secondary'}`}>
                    {speaker}
                  </span>
                  <p className={`type-body ${seg.isProvisional ? 'text-text-quaternary' : 'text-text-secondary'}`}>
                    {seg.text}
                  </p>
                </div>
              )
            })}
          </div>
        </div>
      )}

      <style>{`
        @keyframes mobile-sheet {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
      `}</style>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function normalizeMobileSpeaker(speaker: string): string {
  const lower = speaker.toLowerCase()
  if (lower === 'operator' || lower === 'you') return 'Hisham'
  return speaker
}

function getSessionTitle(type: string | null, text?: string): string {
  if (text) {
    const words = text.split(/\s+/).slice(0, 5).join(' ')
    return words.length < text.length ? `${words}...` : words
  }

  const titles: Record<string, string> = {
    meeting: 'New Meeting',
    thinking: 'Thinking Session',
    planning: 'Planning Session',
    email: 'Email Triage',
    conversation: 'Conversation',
    resume: 'Resumed Session',
  }

  return (type && titles[type]) ?? 'Session'
}
