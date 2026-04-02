import { useEffect, useRef, useCallback } from 'react'
import { useCompanionStore } from '@/store/companion'
import type { AnyCard } from '@/store/companion'
import { useQueryClient } from '@tanstack/react-query'

const SSE_URL = '/companion/stream'

/**
 * useCompanion — connects to the companion SSE stream and populates the store.
 *
 * Events consumed:
 *   transcript     — live speech segments
 *   card           — approval card created / updated
 *   card_status    — card approved / dismissed
 *   context        — reactive context (person, project, topic)
 *   briefing       — morning / evening briefing
 *   voice_state    — idle / listening / processing / speaking
 *   activity       — generic system activity
 *   health         — service health updates
 *   notes          — extracted key points → note groups
 *   research       — entity research results
 *   research_start — entity being researched (loading state)
 *   ideas          — extracted threads / ideas
 *   thinking       — processing status for thinking strip
 *   session        — session state updates
 *   note_group     — structured note group
 */
export function useCompanion() {
  const addStreamItem = useCompanionStore((s) => s.addStreamItem)
  const addSegment = useCompanionStore((s) => s.addSegment)
  const updateSegment = useCompanionStore((s) => s.updateSegment)
  const addCard = useCompanionStore((s) => s.addCard)
  const removeCard = useCompanionStore((s) => s.removeCard)
  const addContextCard = useCompanionStore((s) => s.addContextCard)
  const setBriefing = useCompanionStore((s) => s.setBriefing)
  const setVoiceState = useCompanionStore((s) => s.setVoiceState)
  const setSessionId = useCompanionStore((s) => s.setSessionId)
  const setLastEventSequence = useCompanionStore((s) => s.setLastEventSequence)
  const setThinkingText = useCompanionStore((s) => s.setThinkingText)
  const setSession = useCompanionStore((s) => s.setSession)
  const addNoteGroup = useCompanionStore((s) => s.addNoteGroup)
  const addApproval = useCompanionStore((s) => s.addApproval)
  const removeApproval = useCompanionStore((s) => s.removeApproval)
  const updateSessionTitle = useCompanionStore((s) => s.updateSessionTitle)
  const queryClient = useQueryClient()

  const retryCount = useRef(0)
  const esRef = useRef<EventSource | null>(null)

  // -------------------------------------------------------------------------
  // Convert a card to an approval item (bridge old → new)
  // -------------------------------------------------------------------------
  const cardToApproval = useCallback(
    (card: AnyCard) => {
      const typeMap: Record<string, 'task' | 'decision' | 'vault' | 'reply' | 'system'> = {
        task: 'task',
        decision: 'decision',
        vault: 'vault',
        reply: 'reply',
        system: 'system',
        suggestion: 'system',
      }

      addApproval({
        id: card.id,
        type: typeMap[card.card_type] ?? 'system',
        title: card.title,
        description: card.body,
        metadata: extractCardMetadata(card),
        confidence: card.confidence,
        status: 'pending',
        createdAt: card.created_at,
        card: card,
      })
    },
    [addApproval],
  )

  // -------------------------------------------------------------------------
  // Replay a single event (used for SSE recovery after reconnect)
  // -------------------------------------------------------------------------
  const replayEvent = useCallback(
    (evt: { type: string; data: Record<string, unknown>; seq?: number }) => {
      const d = evt.data
      switch (evt.type) {
        case 'transcript': {
          const segId = (d.id as string) ?? crypto.randomUUID()
          const segText = (d.text as string) ?? ''
          const segSpeaker = (d.speaker as string) ?? 'You'
          const segTime = (d.timestamp as string) ?? new Date().toISOString()
          const isProvisional = (d.is_provisional as boolean) ?? false

          addSegment({
            id: segId,
            speaker: segSpeaker,
            text: segText,
            timestamp: segTime,
            isProvisional,
          })
          if (d.is_update) {
            updateSegment(segId, segText, isProvisional)
          }
          if (d.is_final && !isProvisional) {
            addStreamItem({
              id: segId,
              type: 'transcript',
              timestamp: segTime,
              data: d,
            })
          }
          break
        }
        case 'card':
          addCard(d as unknown as AnyCard)
          cardToApproval(d as unknown as AnyCard)
          break
        case 'card_status':
          if (d.card_id) {
            if (d.status === 'approved' || d.status === 'dismissed') {
              removeCard(d.card_id as string)
              removeApproval(d.card_id as string)
              // Invalidate relevant queries based on action type
              if (d.action === 'create_task' || d.action === 'complete_task') {
                queryClient.invalidateQueries({ queryKey: ['work'] })
              }
              if (d.action === 'lock_decision' || d.action === 'create_inbox') {
                queryClient.invalidateQueries({ queryKey: ['vault'] })
              }
              if (d.action === 'send_message') {
                queryClient.invalidateQueries({ queryKey: ['messages'] })
              }
            }
            // approved_pending — undo window started, don't remove yet
            // pending — undo was triggered, card restored (no action needed,
            //   frontend already manages this via the undo callback)
            // error — action failed, card stays for retry
          }
          break
        case 'context':
          addContextCard({
            id: (d.id as string) ?? crypto.randomUUID(),
            type: (d.context_type as 'person' | 'project' | 'topic' | 'schedule') ?? 'topic',
            title: (d.title as string) ?? '',
            subtitle: d.subtitle as string | undefined,
            data: d,
            timestamp: (d.timestamp as string) ?? new Date().toISOString(),
          })
          break
        case 'briefing':
          setBriefing({
            id: (d.id as string) ?? crypto.randomUUID(),
            summary: (d.summary as string) ?? '',
            schedule: (d.schedule as string[]) ?? [],
            attention: (d.attention as string[]) ?? [],
            metrics: (d.metrics as Record<string, string | number>) ?? {},
            timestamp: (d.timestamp as string) ?? new Date().toISOString(),
          })
          addStreamItem({
            id: (d.id as string) ?? crypto.randomUUID(),
            type: 'briefing',
            timestamp: (d.timestamp as string) ?? new Date().toISOString(),
            data: d,
          })
          break
        case 'voice_state':
          if (d.state) setVoiceState(d.state as 'idle' | 'listening' | 'processing' | 'speaking')
          break
        case 'thinking':
          setThinkingText((d.text as string) ?? null)
          break
        case 'session':
          if (d.session) {
            setSession(d.session as import('@/store/companion').SessionState)
          }
          break
        case 'note_group':
          addNoteGroup(d as unknown as import('@/store/companion').NoteGroup)
          break
        case 'activity':
          addStreamItem({
            id: (d.id as string) ?? crypto.randomUUID(),
            type: 'activity',
            timestamp: (d.timestamp as string) ?? new Date().toISOString(),
            data: d,
          })
          break
        case 'health':
          addStreamItem({
            id: (d.id as string) ?? crypto.randomUUID(),
            type: 'health',
            timestamp: (d.timestamp as string) ?? new Date().toISOString(),
            data: d,
          })
          break
        case 'notes':
          if (d.notes && Array.isArray(d.notes)) {
            for (const note of d.notes as string[]) {
              addStreamItem({
                id: crypto.randomUUID(),
                type: 'activity',
                timestamp: new Date().toISOString(),
                data: { source: 'intelligence', message: note },
              })
            }
          }
          break
        case 'research':
          addContextCard({
            id: `research-${d.entity as string}`,
            type: 'topic',
            title: (d.entity as string) ?? '',
            subtitle: 'Research',
            data: { vault_notes: [d.summary] },
            timestamp: new Date().toISOString(),
          })
          break
        case 'research_start':
          addContextCard({
            id: `research-${d.entity as string}`,
            type: 'topic',
            title: (d.entity as string) ?? '',
            subtitle: 'Researching...',
            data: { loading: true },
            timestamp: new Date().toISOString(),
          })
          break
        case 'ideas':
        case 'companion_ideas':
          if (d.ideas && Array.isArray(d.ideas)) {
            for (const idea of d.ideas as string[]) {
              addStreamItem({
                id: crypto.randomUUID(),
                type: 'activity',
                timestamp: new Date().toISOString(),
                data: { source: 'intelligence', message: idea },
              })
            }
          }
          break
        case 'companion_notes':
          if (d.notes && Array.isArray(d.notes)) {
            for (const note of d.notes as string[]) {
              addStreamItem({
                id: crypto.randomUUID(),
                type: 'activity',
                timestamp: new Date().toISOString(),
                data: { source: 'intelligence', message: note },
              })
            }
          }
          break
        case 'companion_tasks':
          if (d.tasks && Array.isArray(d.tasks)) {
            for (const task of d.tasks as string[]) {
              addStreamItem({
                id: crypto.randomUUID(),
                type: 'activity',
                timestamp: new Date().toISOString(),
                data: { source: 'intelligence', message: `Task: ${task}` },
              })
            }
          }
          break
        case 'companion_research':
          addContextCard({
            id: `research-${d.entity as string}`,
            type: 'topic',
            title: (d.entity as string) ?? '',
            subtitle: 'Research',
            data: { vault_notes: [d.summary] },
            timestamp: new Date().toISOString(),
          })
          break
        case 'companion_research_start':
          addContextCard({
            id: `research-${d.entity as string}`,
            type: 'topic',
            title: (d.entity as string) ?? '',
            subtitle: 'Researching...',
            data: { loading: true },
            timestamp: new Date().toISOString(),
          })
          break
        case 'companion_session_title':
          if (d.title) {
            updateSessionTitle(d.title as string)
          }
          break
        case 'companion_session_started':
          if (d.session_id) {
            setSessionId(d.session_id as string)
          }
          break
        case 'companion_session_ended':
          setSession(null)
          break
      }

      // Track sequence number
      if (typeof evt.seq === 'number') {
        setLastEventSequence(evt.seq)
      }
    },
    [
      addStreamItem,
      addSegment,
      updateSegment,
      addCard,
      removeCard,
      addContextCard,
      setBriefing,
      setVoiceState,
      setSessionId,
      setLastEventSequence,
      setThinkingText,
      setSession,
      addNoteGroup,
      addApproval,
      removeApproval,
      updateSessionTitle,
      cardToApproval,
      queryClient,
    ],
  )

  // -------------------------------------------------------------------------
  // SSE connection with event recovery on reconnect
  // -------------------------------------------------------------------------
  const connect = useCallback(() => {
    let es: EventSource
    try {
      es = new EventSource(SSE_URL)
    } catch {
      return
    }
    esRef.current = es

    es.onopen = () => {
      const wasReconnect = retryCount.current > 0
      retryCount.current = 0

      // Recover missed events on reconnect
      if (wasReconnect) {
        const lastSeq = useCompanionStore.getState().lastEventSequence
        if (lastSeq > 0) {
          fetch(`/companion/session/events?after=${lastSeq}`)
            .then((r) => (r.ok ? r.json() : []))
            .then((events: { type: string; data: Record<string, unknown>; seq?: number }[]) => {
              for (const evt of events) {
                replayEvent(evt)
              }
            })
            .catch(() => {})
        }
      }
    }

    // Helper: track sequence from SSE event data
    const trackSeq = (d: Record<string, unknown>) => {
      if (typeof d.seq === 'number') {
        setLastEventSequence(d.seq as number)
      }
    }

    // --- Transcript ---
    es.addEventListener('transcript', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        if (d.is_update && d.id) {
          updateSegment(d.id, d.text, d.is_provisional ?? false)
        } else {
          addSegment({
            id: d.id ?? crypto.randomUUID(),
            speaker: d.speaker ?? 'You',
            text: d.text ?? '',
            timestamp: d.timestamp ?? new Date().toISOString(),
            isProvisional: d.is_provisional ?? true,
          })
        }
        addStreamItem({
          id: d.id ?? crypto.randomUUID(),
          type: 'transcript',
          timestamp: d.timestamp ?? new Date().toISOString(),
          data: d,
        })
      } catch { /* malformed event */ }
    })

    // --- Cards ---
    es.addEventListener('card', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        const card = d as AnyCard
        addCard(card)
        cardToApproval(card)
      } catch { /* malformed event */ }
    })

    // --- Card status updates (approved/dismissed/approved_pending/pending/error) ---
    es.addEventListener('card_status', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        if (d.card_id) {
          if (d.status === 'approved' || d.status === 'dismissed') {
            removeCard(d.card_id)
            removeApproval(d.card_id)
            // Invalidate relevant queries based on action type
            if (d.action === 'create_task' || d.action === 'complete_task') {
              queryClient.invalidateQueries({ queryKey: ['work'] })
            }
            if (d.action === 'lock_decision' || d.action === 'create_inbox') {
              queryClient.invalidateQueries({ queryKey: ['vault'] })
            }
            if (d.action === 'send_message') {
              queryClient.invalidateQueries({ queryKey: ['messages'] })
            }
          }
          // approved_pending and pending statuses are managed by the
          // ApprovalItem component's internal undo timer — no store
          // manipulation needed here.
        }
      } catch { /* malformed event */ }
    })

    // --- Context ---
    es.addEventListener('context', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        addContextCard({
          id: d.id ?? crypto.randomUUID(),
          type: d.context_type ?? 'topic',
          title: d.title ?? '',
          subtitle: d.subtitle,
          data: d,
          timestamp: d.timestamp ?? new Date().toISOString(),
        })
      } catch { /* malformed event */ }
    })

    // --- Briefing ---
    es.addEventListener('briefing', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        setBriefing({
          id: d.id ?? crypto.randomUUID(),
          summary: d.summary ?? '',
          schedule: d.schedule ?? [],
          attention: d.attention ?? [],
          metrics: d.metrics ?? {},
          timestamp: d.timestamp ?? new Date().toISOString(),
        })
        addStreamItem({
          id: d.id ?? crypto.randomUUID(),
          type: 'briefing',
          timestamp: d.timestamp ?? new Date().toISOString(),
          data: d,
        })
      } catch { /* malformed event */ }
    })

    // --- Voice state ---
    es.addEventListener('voice_state', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        if (d.state) setVoiceState(d.state)
      } catch { /* malformed event */ }
    })

    // --- Thinking ---
    es.addEventListener('thinking', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        setThinkingText(d.text ?? null)
      } catch { /* malformed event */ }
    })

    // --- Session state ---
    es.addEventListener('session', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        if (d.session) setSession(d.session)
      } catch { /* malformed event */ }
    })

    // --- Note groups ---
    es.addEventListener('note_group', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        addNoteGroup(d)
      } catch { /* malformed event */ }
    })

    // --- Activity ---
    es.addEventListener('activity', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        addStreamItem({
          id: d.id ?? crypto.randomUUID(),
          type: 'activity',
          timestamp: d.timestamp ?? new Date().toISOString(),
          data: d,
        })
      } catch { /* malformed event */ }
    })

    // --- Health ---
    es.addEventListener('health', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        addStreamItem({
          id: d.id ?? crypto.randomUUID(),
          type: 'health',
          timestamp: d.timestamp ?? new Date().toISOString(),
          data: d,
        })
      } catch { /* malformed event */ }
    })

    // --- Notes (intelligence engine key points) ---
    // Backend emits "companion_notes"; also handle legacy "notes"
    const handleNotes = (e: MessageEvent) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        if (d.notes && Array.isArray(d.notes)) {
          for (const note of d.notes) {
            addStreamItem({
              id: crypto.randomUUID(),
              type: 'activity',
              timestamp: new Date().toISOString(),
              data: { source: 'intelligence', message: note },
            })
          }
        }
      } catch { /* malformed event */ }
    }
    es.addEventListener('notes', handleNotes)
    es.addEventListener('companion_notes', handleNotes)

    // --- Research results ---
    // Backend emits "companion_research"; also handle legacy "research"
    const handleResearch = (e: MessageEvent) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        addContextCard({
          id: `research-${d.entity}`,
          type: 'topic',
          title: d.entity,
          subtitle: 'Research',
          data: { vault_notes: [d.summary] },
          timestamp: new Date().toISOString(),
        })
      } catch { /* malformed event */ }
    }
    es.addEventListener('research', handleResearch)
    es.addEventListener('companion_research', handleResearch)

    // --- Research start (loading state) ---
    const handleResearchStart = (e: MessageEvent) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        addContextCard({
          id: `research-${d.entity}`,
          type: 'topic',
          title: d.entity,
          subtitle: 'Researching...',
          data: { loading: true },
          timestamp: new Date().toISOString(),
        })
      } catch { /* malformed event */ }
    }
    es.addEventListener('research_start', handleResearchStart)
    es.addEventListener('companion_research_start', handleResearchStart)

    // --- Ideas (intelligence engine threads) ---
    const handleIdeas = (e: MessageEvent) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        if (d.ideas && Array.isArray(d.ideas)) {
          for (const idea of d.ideas) {
            addStreamItem({
              id: crypto.randomUUID(),
              type: 'activity',
              timestamp: new Date().toISOString(),
              data: { source: 'intelligence', message: idea },
            })
          }
        }
      } catch { /* malformed event */ }
    }
    es.addEventListener('ideas', handleIdeas)
    es.addEventListener('companion_ideas', handleIdeas)

    // --- Tasks (intelligence engine action items) ---
    es.addEventListener('companion_tasks', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        if (d.tasks && Array.isArray(d.tasks)) {
          for (const task of d.tasks) {
            addStreamItem({
              id: crypto.randomUUID(),
              type: 'activity',
              timestamp: new Date().toISOString(),
              data: { source: 'intelligence', message: `Task: ${task}` },
            })
          }
        }
      } catch { /* malformed event */ }
    })

    // --- Suggestion (intelligence engine follow-up) ---
    es.addEventListener('companion_suggestion', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        if (d.text) {
          addStreamItem({
            id: crypto.randomUUID(),
            type: 'activity',
            timestamp: new Date().toISOString(),
            data: { source: 'intelligence', message: d.text },
          })
        }
      } catch { /* malformed event */ }
    })

    // --- Session lifecycle ---
    es.addEventListener('companion_session_started', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        if (d.session_id) {
          setSessionId(d.session_id)
        }
      } catch { /* malformed event */ }
    })

    es.addEventListener('companion_session_ended', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        setSession(null)
        addStreamItem({
          id: crypto.randomUUID(),
          type: 'activity',
          timestamp: new Date().toISOString(),
          data: { source: 'session', message: 'Session ended' },
        })
      } catch { /* malformed event */ }
    })

    es.addEventListener('companion_session_paused', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        // Refresh session state from server
        fetch('/companion/session')
          .then((r) => (r.ok ? r.json() : null))
          .then((data) => {
            if (data) setSession(data)
          })
          .catch(() => {})
      } catch { /* malformed event */ }
    })

    es.addEventListener('companion_session_resumed', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        fetch('/companion/session')
          .then((r) => (r.ok ? r.json() : null))
          .then((data) => {
            if (data) setSession(data)
          })
          .catch(() => {})
      } catch { /* malformed event */ }
    })

    // --- Session auto-title ---
    es.addEventListener('companion_session_title', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        if (d.title) {
          updateSessionTitle(d.title)
        }
      } catch { /* malformed event */ }
    })

    // --- Reconnect on error ---
    es.onerror = () => {
      es.close()
      esRef.current = null
      const delay = Math.min(1000 * Math.pow(2, retryCount.current), 30000)
      retryCount.current++
      setTimeout(connect, delay)
    }
  }, [
    addStreamItem,
    addSegment,
    updateSegment,
    addCard,
    removeCard,
    addContextCard,
    setBriefing,
    setVoiceState,
    setSessionId,
    setLastEventSequence,
    setThinkingText,
    setSession,
    addNoteGroup,
    addApproval,
    removeApproval,
    updateSessionTitle,
    cardToApproval,
    replayEvent,
    queryClient,
  ])

  // -------------------------------------------------------------------------
  // Session lifecycle — resume or start a session on mount
  // -------------------------------------------------------------------------
  const sessionInitialized = useRef(false)
  useEffect(() => {
    if (sessionInitialized.current) return
    sessionInitialized.current = true

    const existingSessionId = useCompanionStore.getState().sessionId
    if (existingSessionId) {
      // Verify the existing session is still active
      fetch('/companion/session')
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (data?.session?.status === 'active') {
            return
          }
          return fetch('/companion/session/start', { method: 'POST' })
            .then((r) => r.json())
            .then((session) => {
              if (session?.id) setSessionId(session.id)
            })
        })
        .catch(() => {})
    } else {
      fetch('/companion/session/start', { method: 'POST' })
        .then((r) => r.json())
        .then((session) => {
          if (session?.id) setSessionId(session.id)
        })
        .catch(() => {})
    }
  }, [setSessionId])

  // Fetch initial briefing on mount
  const briefingFetched = useRef(false)
  useEffect(() => {
    if (briefingFetched.current) return
    briefingFetched.current = true

    fetch('/companion/briefing')
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (data && data.summary) {
          setBriefing({
            id: data.id ?? crypto.randomUUID(),
            summary: data.summary,
            schedule: data.schedule ?? [],
            attention: data.attention ?? [],
            metrics: data.metrics ?? {},
            timestamp: data.timestamp ?? new Date().toISOString(),
          })
        }
      })
      .catch(() => { /* briefing optional */ })
  }, [setBriefing])

  useEffect(() => {
    connect()
    return () => {
      esRef.current?.close()
      esRef.current = null
    }
  }, [connect])

  return {
    stream: useCompanionStore((s) => s.stream),
    segments: useCompanionStore((s) => s.segments),
    contextCards: useCompanionStore((s) => s.contextCards),
    voiceState: useCompanionStore((s) => s.voiceState),
    briefing: useCompanionStore((s) => s.briefing),
    cards: useCompanionStore((s) => s.cards),
    session: useCompanionStore((s) => s.session),
    noteGroups: useCompanionStore((s) => s.noteGroups),
    approvals: useCompanionStore((s) => s.approvals),
  }
}

// ---------------------------------------------------------------------------
// Helper: extract metadata from a card for the approval view
// ---------------------------------------------------------------------------
function extractCardMetadata(card: AnyCard): Record<string, unknown> {
  const meta: Record<string, unknown> = {}

  if ('task_title' in card) meta.task = card.task_title
  if ('task_project' in card && card.task_project) meta.project = card.task_project
  if ('task_priority' in card) meta.priority = card.task_priority
  if ('task_due' in card && card.task_due) meta.due = card.task_due
  if ('rationale' in card) meta.rationale = card.rationale
  if ('stakeholders' in card && card.stakeholders?.length) meta.stakeholders = card.stakeholders.join(', ')
  if ('note_type' in card) meta.noteType = card.note_type
  if ('suggested_path' in card) meta.path = card.suggested_path
  if ('channel' in card) meta.channel = card.channel
  if ('recipient' in card) meta.recipient = card.recipient
  if ('draft_text' in card) meta.draft = card.draft_text
  if ('service_name' in card) meta.service = card.service_name
  if ('severity' in card) meta.severity = card.severity
  if ('suggested_action' in card) meta.action = card.suggested_action

  return meta
}
