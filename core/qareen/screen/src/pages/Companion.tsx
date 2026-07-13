import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useCompanion } from '@/hooks/useCompanion'
import { useCompanionStore } from '@/store/companion'
import { ContextBar } from '@/components/companion/ContextBar'
import { SessionLauncher } from '@/components/companion/SessionLauncher'
import { SessionSetup, type SessionConfig } from '@/components/companion/SessionSetup'

// ---------------------------------------------------------------------------
// Home — launcher screen at /
//
// Two states:
//   1. idle → SessionLauncher (greeting + orb + pills)
//   2. setup → SessionSetup (glass card for intent + context)
//
// Starting a session navigates to /companion/session/:id
// ---------------------------------------------------------------------------

export default function Companion() {
  useCompanion()
  const navigate = useNavigate()

  const setSession = useCompanionStore((s) => s.setSession)
  const clearSessionData = useCompanionStore((s) => s.clearSessionData)

  const [setupMode, setSetupMode] = useState(false)
  const [setupInitialText, setSetupInitialText] = useState('')
  const [setupSkill, setSetupSkill] = useState<string | null>(null)
  const [transitioning, setTransitioning] = useState(false)

  const handleEnterSetup = useCallback((type: string, text?: string) => {
    setSetupInitialText(text ?? '')
    setSetupSkill(type)
    setTransitioning(true)
    setTimeout(() => {
      setSetupMode(true)
      setTransitioning(false)
    }, 200)
  }, [])

  const handleSetupStart = useCallback(
    (config: SessionConfig) => {
      clearSessionData()

      const sessionId = crypto.randomUUID()
      const title = getSessionTitle(config.skill, config.intent)

      const newSession = {
        id: sessionId,
        title,
        type: config.type,
        skill: config.skill,
        startedAt: new Date().toISOString(),
        status: 'active' as const,
        stats: { processed: 0, total: 0, approved: 0 },
      }

      setSession(newSession)

      // End any stale backend session, then start the new one
      ;(async () => {
        try {
          const res = await fetch('/companion/session')
          if (res.ok) {
            const active = await res.json()
            if (active?.id && active.status === 'active') {
              await fetch(`/companion/session/${active.id}/end`, { method: 'POST' })
            }
          }
        } catch { /* best effort */ }

        fetch('/companion/session/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            type: config.type,
            skill: newSession.skill,
            title,
            people: config.people,
            context: config.context,
          }),
        }).catch(() => {})

        if (config.intent) {
          fetch('/companion/input', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: config.intent, source: 'text' }),
          }).catch(() => {})
        }
      })()

      // Navigate immediately — mic auto-activates on session page
      navigate(`/companion/session/${sessionId}`)
    },
    [setSession, clearSessionData, navigate],
  )

  const handleSetupCancel = useCallback(() => {
    setTransitioning(true)
    setTimeout(() => {
      setSetupMode(false)
      setTransitioning(false)
    }, 200)
  }, [])

  return (
    <div className="flex flex-col h-full relative overflow-hidden bg-[#0A0806]">
      <ContextBar />

      <div className="relative z-10 flex flex-col h-full">
        {!setupMode && (
          <div className={`flex flex-col h-full ${transitioning ? 'companion-fade-out' : 'companion-fade-in'}`} style={{ opacity: 0 }}>
            <SessionLauncher onStartSession={(type, text) => handleEnterSetup(type, text)} />
          </div>
        )}

        {setupMode && (
          <div className={`flex flex-col h-full ${transitioning ? 'companion-fade-out' : 'companion-fade-in'}`}>
            <SessionSetup
              initialText={setupInitialText}
              initialSkill={setupSkill}
              onStart={handleSetupStart}
              onCancel={handleSetupCancel}
            />
          </div>
        )}
      </div>

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
      `}</style>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
