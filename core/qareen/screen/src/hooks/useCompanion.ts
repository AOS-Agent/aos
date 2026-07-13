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
export function useCompanion(options?: { enableRestore?: boolean }) {
  const enableRestore = options?.enableRestore ?? false
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
  const addThread = useCompanionStore((s) => s.addThread)
  const addUnitToThread = useCompanionStore((s) => s.addUnitToThread)
  const setActiveThread = useCompanionStore((s) => s.setActiveThread)
  const updateThreadSummary = useCompanionStore((s) => s.updateThreadSummary)
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
        case 'stream.unit': {
          const u = d
          addUnitToThread({
            id: (u.id as string) ?? crypto.randomUUID(),
            threadId: (u.thread_id as string) ?? '',
            text: (u.text as string) ?? '',
            speaker: (u.speaker as string) ?? '',
            timestamp: (u.timestamp as string) ?? new Date().toISOString(),
            classification: (u.classification as import('@/store/companion').ThoughtUnit['classification']) ?? 'context',
            confidence: (u.confidence as number) ?? 0,
            entities: (u.entities as string[]) ?? [],
          })
          break
        }
        case 'stream.thread_new': {
          addThread({
            id: (d.thread_id as string) ?? crypto.randomUUID(),
            title: (d.title as string) ?? '',
            summary: '',
            isActive: true,
            firstSeen: new Date().toISOString(),
            lastSeen: new Date().toISOString(),
          })
          break
        }
        case 'stream.thread_switch': {
          if (d.to) setActiveThread(d.to as string)
          break
        }
        case 'stream.thread_update': {
          if (d.thread_id && d.summary) {
            updateThreadSummary(d.thread_id as string, d.summary as string)
          }
          break
        }
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
      addThread,
      addUnitToThread,
      setActiveThread,
      updateThreadSummary,
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
            .then((events: unknown) => {
              if (!Array.isArray(events)) return
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

    // --- Thread visualization (v2) ---
    es.addEventListener('stream.unit', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        addUnitToThread({
          id: d.id ?? crypto.randomUUID(),
          threadId: d.thread_id ?? '',
          text: d.text ?? '',
          speaker: d.speaker ?? '',
          timestamp: d.timestamp ?? new Date().toISOString(),
          classification: d.classification ?? 'context',
          confidence: d.confidence ?? 0,
          entities: d.entities ?? [],
        })
      } catch { /* malformed event */ }
    })

    es.addEventListener('stream.thread_new', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        addThread({
          id: d.thread_id ?? crypto.randomUUID(),
          title: d.title ?? '',
          summary: '',
          isActive: true,
          firstSeen: new Date().toISOString(),
          lastSeen: new Date().toISOString(),
        })
      } catch { /* malformed event */ }
    })

    es.addEventListener('stream.thread_switch', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        if (d.to) setActiveThread(d.to)
      } catch { /* malformed event */ }
    })

    es.addEventListener('stream.thread_update', (e) => {
      try {
        const d = JSON.parse(e.data)
        trackSeq(d)
        if (d.thread_id && d.summary) {
          updateThreadSummary(d.thread_id, d.summary)
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
    addThread,
    addUnitToThread,
    setActiveThread,
    updateThreadSummary,
    cardToApproval,
    replayEvent,
    queryClient,
  ])

  // -------------------------------------------------------------------------
  // Session lifecycle — check for existing active session on mount
  // -------------------------------------------------------------------------
  const sessionInitialized = useRef(false)
  useEffect(() => {
    if (sessionInitialized.current) return
    sessionInitialized.current = true

    // Check backend for an active session — don't auto-create one.
    // Sessions are created explicitly by the user via SessionSetup.
    fetch('/companion/session')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data && data.status === 'active') {
          // Active session exists — set ID so SSE event recovery works.
          // Full state restore (transcript, notes, cards) handled by the
          // separate sessionRestored effect below.
          setSessionId(data.id)
        }
      })
      .catch(() => {})
  }, [setSessionId])

  // -------------------------------------------------------------------------
  // Restore active session from backend on mount (survives page refresh).
  // Only enabled on session pages — prevents home page from pulling stale data.
  // -------------------------------------------------------------------------
  const sessionRestored = useRef(false)
  useEffect(() => {
    if (!enableRestore) return
    if (sessionRestored.current) return
    sessionRestored.current = true
    let cancelled = false

    async function restoreSession() {
      try {
        const res = await fetch('/companion/session')
        if (!res.ok) return
        const data = await res.json()

        if (cancelled || !data || data.status !== 'active') return

        // Only restore sessions that have actual content — skip empty orphans
        const hasContent = (data.utterance_count || 0) > 0
          || (Array.isArray(data.transcript_json) && data.transcript_json.length > 0)
        if (!hasContent) return

        // Only restore if store has no session (e.g. after page refresh cleared sessionStorage)
        const storeState = useCompanionStore.getState()
        if (storeState.session) return

        // Map backend dict → SessionState
        storeState.setSession({
          id: data.id,
          title: data.title || 'Restored Session',
          type: data.session_type || 'conversation',
          skill: data.skill ?? null,
          startedAt: data.started_at,
          status: 'active',
          stats: {
            processed: data.utterance_count || 0,
            total: data.utterance_count || 0,
            approved: data.approvals_approved || 0,
          },
        })

        // Also set the sessionId for SSE event recovery
        if (data.id) {
          storeState.setSessionId(data.id)
        }

        // Restore transcript segments
        if (data.transcript_json && Array.isArray(data.transcript_json)) {
          for (const block of data.transcript_json) {
            if (cancelled) return
            storeState.addSegment({
              id: `restored-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
              speaker: block.speaker || 'You',
              text: block.text || '',
              timestamp: block.timestamp || new Date().toISOString(),
              isProvisional: false,
            })
          }
        }

        // Restore note groups
        if (data.notes_json && Array.isArray(data.notes_json)) {
          for (const group of data.notes_json) {
            if (cancelled) return
            storeState.addNoteGroup(group)
          }
        }

        // Restore cards as approvals
        if (data.cards_json && Array.isArray(data.cards_json)) {
          const typeMap: Record<string, 'task' | 'decision' | 'vault' | 'reply' | 'system'> = {
            task: 'task', decision: 'decision', vault: 'vault',
            reply: 'reply', system: 'system', suggestion: 'system',
          }
          for (const card of data.cards_json) {
            if (cancelled) return
            storeState.addCard(card)
            storeState.addApproval({
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
          }
        }
      } catch {
        // Silent fail — session restore is best-effort
      }
    }

    // Small delay to let SSE connect first
    const timer = setTimeout(() => {
      if (!cancelled) restoreSession()
    }, 500)

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [])

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
    threads: useCompanionStore((s) => s.threads),
    activeThreadId: useCompanionStore((s) => s.activeThreadId),
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
