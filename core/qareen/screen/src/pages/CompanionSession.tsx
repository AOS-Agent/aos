import { useState, useCallback, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useCompanion } from '@/hooks/useCompanion'
import { useCompanionStore } from '@/store/companion'
import { useApprovals } from '@/hooks/useApprovals'
import { TranscriptPanel } from '@/components/companion/TranscriptPanel'
import { WorkspacePanel } from '@/components/companion/WorkspacePanel'
import { ThinkingStrip } from '@/components/companion/ThinkingStrip'
import { UnifiedInput } from '@/components/companion/UnifiedInput'
import { ThreadCanvas } from '@/components/companion/ThreadCanvas'
import { SessionTray } from '@/components/companion/SessionTray'
import { Pause, Square, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import type { SessionState } from '@/store/companion'

// ---------------------------------------------------------------------------
// CompanionSession — active session workspace at /companion/session/:id
//
// Two modes: Focus (ThreadCanvas) and Split (Transcript + Workspace).
// Session auto-titles from initial prompt or content.
// ---------------------------------------------------------------------------

export default function CompanionSession() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()

  // Connect SSE stream + enable session restore after refresh
  useCompanion({ enableRestore: true })

  const session = useCompanionStore((s) => s.session)
  const setSession = useCompanionStore((s) => s.setSession)
  const addPausedSession = useCompanionStore((s) => s.addPausedSession)
  const transcriptCollapsed = useCompanionStore((s) => s.transcriptCollapsed)
  const setTranscriptCollapsed = useCompanionStore((s) => s.setTranscriptCollapsed)
  const noteGroups = useCompanionStore((s) => s.noteGroups)
  const approvals = useCompanionStore((s) => s.approvals)
  const threads = useCompanionStore((s) => s.threads)
  const activeThreadId = useCompanionStore((s) => s.activeThreadId)
  const screenMode = useCompanionStore((s) => s.screenMode)
  const setScreenMode = useCompanionStore((s) => s.setScreenMode)
  const voiceState = useCompanionStore((s) => s.voiceState)
  const clearSessionData = useCompanionStore((s) => s.clearSessionData)
  const updateSessionTitle = useCompanionStore((s) => s.updateSessionTitle)
  const { approve, startApproval, undo, dismiss, edit } = useApprovals()

  // If no session in store, try to restore from backend
  useEffect(() => {
    if (session) return
    if (!sessionId) { navigate('/'); return }

    fetch(`/companion/session/${sessionId}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data || data.status === 'ended') {
          navigate('/')
          return
        }
        setSession({
          id: data.id,
          title: data.title || 'Session',
          type: data.session_type || 'conversation',
          skill: data.skill,
          startedAt: data.started_at,
          status: 'active',
          stats: { processed: 0, total: 0, approved: 0 },
        })
      })
      .catch(() => navigate('/'))
  }, [sessionId, session, setSession, navigate])

  // Title editing
  const [isEditingTitle, setIsEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')

  const startEditTitle = useCallback(() => {
    setTitleDraft(session?.title ?? '')
    setIsEditingTitle(true)
  }, [session?.title])

  const commitTitle = useCallback(() => {
    const trimmed = titleDraft.trim()
    if (trimmed && trimmed !== session?.title) {
      updateSessionTitle(trimmed)
      fetch(`/companion/session/${sessionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: trimmed }),
      }).catch(() => {})
    }
    setIsEditingTitle(false)
  }, [titleDraft, session?.title, sessionId, updateSessionTitle])

  // Pause → go home
  const handlePause = useCallback(() => {
    if (!session) return
    addPausedSession({ id: session.id, title: session.title, type: session.type, pausedAt: new Date().toISOString() })
    setSession(null)
    fetch('/companion/session/pause', { method: 'POST' }).catch(() => {})
    navigate('/')
  }, [session, setSession, addPausedSession, navigate])

  // Stop → end session, go home
  const handleStop = useCallback(() => {
    if (!session) return
    fetch(`/companion/session/${session.id}/end`, { method: 'POST' }).catch(() => {})
    clearSessionData()
    navigate('/')
  }, [session, clearSessionData, navigate])

  // Send text
  const handleSendText = useCallback((text: string) => {
    if (!text.trim()) return
    fetch('/companion/input', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text.trim(), source: 'text' }),
    }).catch(() => {})
  }, [])

  // Approval handlers
  const handleApprove = useCallback((id: string) => approve(id), [approve])
  const handleStartApproval = useCallback((id: string) => startApproval(id), [startApproval])
  const handleUndo = useCallback((id: string) => undo(id), [undo])
  const handleDismiss = useCallback((id: string) => dismiss(id), [dismiss])
  const handleEdit = useCallback((id: string) => edit(id, {}), [edit])

  // Keyboard shortcuts
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if (e.metaKey || e.ctrlKey || e.altKey) return

      if (!session) return
      const pending = approvals.find(a => a.status === 'pending')
      const undoing = approvals.find(a => a.status === 'approved_pending')

      switch (e.key.toLowerCase()) {
        case 'a': if (pending) { e.preventDefault(); handleStartApproval(pending.id) } break
        case 'd': if (pending) { e.preventDefault(); handleDismiss(pending.id) } break
        case 'u': if (undoing) { e.preventDefault(); handleUndo(undoing.id) } break
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [session, approvals, handleStartApproval, handleDismiss, handleUndo])

  if (!session) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-1.5 rounded-full bg-text-quaternary animate-pulse" />
          <span className="text-[12px] text-text-quaternary">Loading session...</span>
        </div>
      </div>
    )
  }

  const pendingCount = approvals.filter(a => a.status === 'pending').length

  return (
    <div className="flex flex-col h-full relative overflow-hidden">
      {/* ── Session header — glass pill, top-right ── */}
      <div
        className="fixed top-3 right-3 z-[320] flex items-center gap-2 h-8 px-3 rounded-full"
        style={{
          background: 'var(--glass-bg)',
          backdropFilter: 'blur(12px)',
          border: '1px solid var(--glass-border)',
          boxShadow: 'var(--glass-shadow)',
        }}
      >
        {/* Editable title */}
        {isEditingTitle ? (
          <input
            autoFocus
            value={titleDraft}
            onChange={e => setTitleDraft(e.target.value)}
            onBlur={commitTitle}
            onKeyDown={e => { if (e.key === 'Enter') commitTitle(); if (e.key === 'Escape') setIsEditingTitle(false) }}
            className="w-32 h-5 px-1 bg-transparent border-b border-accent/40 text-[12px] font-[510] text-text focus:outline-none"
          />
        ) : (
          <button
            onClick={startEditTitle}
            className="truncate text-[12px] font-[510] text-text-secondary hover:text-text transition-colors max-w-[200px] cursor-pointer"
            title="Click to edit title"
          >
            {session.title}
          </button>
        )}

        <div className="w-px h-3.5 bg-border/40" />

        {/* Transcript toggle */}
        {screenMode === 'split' && (
          <button
            onClick={() => setTranscriptCollapsed(!transcriptCollapsed)}
            className="h-5 w-5 rounded-full inline-flex items-center justify-center text-text-tertiary hover:text-text transition-colors cursor-pointer"
            style={{ transitionDuration: '80ms' }}
            title={transcriptCollapsed ? 'Show transcript' : 'Hide transcript'}
          >
            {transcriptCollapsed ? <PanelLeftOpen className="w-3 h-3" /> : <PanelLeftClose className="w-3 h-3" />}
          </button>
        )}

        {/* Pause */}
        <button
          onClick={handlePause}
          className="h-5 w-5 rounded-full inline-flex items-center justify-center text-text-tertiary hover:text-yellow transition-colors cursor-pointer"
          style={{ transitionDuration: '80ms' }}
          title="Pause session"
        >
          <Pause className="w-3 h-3" />
        </button>

        {/* Stop */}
        <button
          onClick={handleStop}
          className="h-5 w-5 rounded-full inline-flex items-center justify-center text-text-tertiary hover:text-red transition-colors cursor-pointer"
          style={{ transitionDuration: '80ms' }}
          title="End session"
        >
          <Square className="w-3 h-3" />
        </button>
      </div>

      {/* ── Mode toggle — centered pill ── */}
      <div
        className="fixed top-3 left-1/2 -translate-x-1/2 z-[320] flex items-center gap-1 h-7 px-1 rounded-full"
        style={{ background: 'var(--glass-bg)', backdropFilter: 'blur(12px)', border: '1px solid var(--glass-border)' }}
      >
        <button
          onClick={() => setScreenMode('focus')}
          className={`px-2.5 h-5 rounded-full text-[10px] font-[510] cursor-pointer transition-colors ${screenMode === 'focus' ? 'bg-[rgba(255,245,235,0.10)] text-text' : 'text-text-quaternary hover:text-text-tertiary'}`}
          style={{ transitionDuration: '80ms' }}
        >
          Focus
        </button>
        <button
          onClick={() => setScreenMode('split')}
          className={`px-2.5 h-5 rounded-full text-[10px] font-[510] cursor-pointer transition-colors ${screenMode === 'split' ? 'bg-[rgba(255,245,235,0.10)] text-text' : 'text-text-quaternary hover:text-text-tertiary'}`}
          style={{ transitionDuration: '80ms' }}
        >
          Split
        </button>
      </div>

      {/* ── Workspace content ── */}
      {screenMode === 'focus' ? (
        <div className="flex-1 flex flex-col p-3 pt-14 min-h-0">
          <div className="flex-1 overflow-y-auto min-h-0">
            <div className="max-w-[680px] mx-auto">
              <ThreadCanvas threads={threads} activeThreadId={activeThreadId} />
            </div>
          </div>
          <div className="mt-2">
            <SessionTray
              pendingCards={pendingCount}
              activeAgents={0}
              sessionSeconds={Math.floor((Date.now() - new Date(session.startedAt).getTime()) / 1000)}
              voiceState={voiceState}
              onExpandCards={() => setScreenMode('split')}
            />
          </div>
        </div>
      ) : (
        <div className="flex-1 flex gap-3 p-3 pt-14 min-h-0">
          <div
            className={`hidden md:flex shrink-0 ${transcriptCollapsed ? 'w-12' : 'w-[38%] min-w-[280px] max-w-[480px]'} rounded-[14px] overflow-hidden flex-col transition-all duration-[220ms]`}
            style={{
              background: 'rgba(21,18,16,0.50)',
              backdropFilter: 'blur(20px)',
              border: '1px solid rgba(255,245,235,0.08)',
              boxShadow: '0 4px 24px rgba(0,0,0,0.3)',
            }}
          >
            <TranscriptPanel />
          </div>
          <div
            className="flex-1 min-w-0 rounded-[14px] overflow-hidden flex flex-col"
            style={{
              background: 'rgba(21,18,16,0.50)',
              backdropFilter: 'blur(20px)',
              border: '1px solid rgba(255,245,235,0.08)',
              boxShadow: '0 4px 24px rgba(0,0,0,0.3)',
            }}
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
      )}

      {/* Thinking strip */}
      <ThinkingStrip />

      {/* Input bar */}
      <div className="px-3 pb-3">
        <UnifiedInput onSendText={handleSendText} autoActivateMic />
      </div>
    </div>
  )
}
