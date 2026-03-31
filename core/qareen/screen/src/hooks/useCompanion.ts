import { useEffect, useRef, useCallback } from 'react'
import { useCompanionStore } from '@/store/companion'
import type { AnyCard } from '@/store/companion'
import { useQueryClient } from '@tanstack/react-query'

const SSE_URL = '/companion/stream'

/**
 * useCompanion — connects to the companion SSE stream and populates the store.
 *
 * Events consumed:
 *   transcript  — live speech segments
 *   card        — approval card created / updated
 *   context     — reactive context (person, project, topic)
 *   briefing    — morning / evening briefing
 *   voice_state — idle / listening / processing / speaking
 *   activity    — generic system activity
 *   health      — service health updates
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
  const queryClient = useQueryClient()

  const retryCount = useRef(0)
  const esRef = useRef<EventSource | null>(null)

  const connect = useCallback(() => {
    let es: EventSource
    try {
      es = new EventSource(SSE_URL)
    } catch {
      return
    }
    esRef.current = es

    es.onopen = () => {
      retryCount.current = 0
    }

    // --- Transcript ---
    es.addEventListener('transcript', (e) => {
      try {
        const d = JSON.parse(e.data)
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
        const d = JSON.parse(e.data) as AnyCard
        addCard(d)
      } catch { /* malformed event */ }
    })

    // --- Card status updates (approved/dismissed) ---
    es.addEventListener('card_status', (e) => {
      try {
        const d = JSON.parse(e.data)
        if (d.card_id && (d.status === 'approved' || d.status === 'dismissed')) {
          removeCard(d.card_id)
          // Invalidate work queries so task lists refresh
          if (d.action === 'create_task' || d.action === 'complete_task') {
            queryClient.invalidateQueries({ queryKey: ['work'] })
          }
        }
      } catch { /* malformed event */ }
    })

    // --- Context ---
    es.addEventListener('context', (e) => {
      try {
        const d = JSON.parse(e.data)
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
        if (d.state) setVoiceState(d.state)
      } catch { /* malformed event */ }
    })

    // --- Activity ---
    es.addEventListener('activity', (e) => {
      try {
        const d = JSON.parse(e.data)
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
        addStreamItem({
          id: d.id ?? crypto.randomUUID(),
          type: 'health',
          timestamp: d.timestamp ?? new Date().toISOString(),
          data: d,
        })
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
    queryClient,
  ])

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
  }
}
