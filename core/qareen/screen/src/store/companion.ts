import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import type {
  Card,
  TaskCard,
  DecisionCard,
  VaultCard,
  ReplyCard,
  SystemCard,
  SuggestionCard,
} from '@/lib/types'

// ---------------------------------------------------------------------------
// Voice state
// ---------------------------------------------------------------------------

export type VoiceState = 'idle' | 'listening' | 'processing' | 'speaking'

// ---------------------------------------------------------------------------
// Session types
// ---------------------------------------------------------------------------

export interface SessionState {
  id: string
  title: string
  type: 'conversation' | 'processing'
  skill: string | null
  startedAt: string
  status: 'active' | 'paused'
  stats: { processed: number; total: number; approved: number }
}

export interface PausedSession {
  id: string
  title: string
  type: 'conversation' | 'processing'
  pausedAt: string
}

// ---------------------------------------------------------------------------
// Transcript segment
// ---------------------------------------------------------------------------

export interface TranscriptSegment {
  id: string
  speaker: string
  text: string
  timestamp: string
  isProvisional: boolean
}

// ---------------------------------------------------------------------------
// Workspace — structured notes
// ---------------------------------------------------------------------------

export interface NoteGroup {
  id: string
  title: string
  bullets: NoteBullet[]
  entityTags: EntityTag[]
  timestamp: string
  isPinned: boolean
}

export interface NoteBullet {
  id: string
  text: string
  isEditing: boolean
  type: 'note' | 'action' | 'decision' | 'insight'
}

export interface EntityTag {
  id: string
  name: string
  type: 'person' | 'project' | 'topic'
  entityId?: string
}

// ---------------------------------------------------------------------------
// Approval items
// ---------------------------------------------------------------------------

export interface ApprovalItem {
  id: string
  type: 'task' | 'decision' | 'vault' | 'reply' | 'system'
  title: string
  description: string
  metadata: Record<string, unknown>
  confidence: number
  status: 'pending' | 'approved_pending' | 'approved' | 'dismissed'
  undoTimer?: number
  createdAt: string
  // Carry original card data for rendering
  card?: Card
}

// ---------------------------------------------------------------------------
// Focus anchor
// ---------------------------------------------------------------------------

export interface FocusAnchor {
  skillName: string
  current: number
  total: number
  label: string
}

// ---------------------------------------------------------------------------
// Context card (legacy — still fed by SSE)
// ---------------------------------------------------------------------------

export type ContextCardType = 'person' | 'project' | 'topic' | 'schedule'

export interface ContextCardData {
  id: string
  type: ContextCardType
  title: string
  subtitle?: string
  data: Record<string, unknown>
  timestamp: string
}

// ---------------------------------------------------------------------------
// Briefing
// ---------------------------------------------------------------------------

export interface Briefing {
  id: string
  summary: string
  schedule: string[]
  attention: string[]
  metrics: Record<string, string | number>
  timestamp: string
}

// ---------------------------------------------------------------------------
// Stream item (legacy)
// ---------------------------------------------------------------------------

export type StreamItemType =
  | 'transcript'
  | 'activity'
  | 'health'
  | 'briefing'
  | 'system'

export interface StreamItem {
  id: string
  type: StreamItemType
  timestamp: string
  data: Record<string, unknown>
}

// ---------------------------------------------------------------------------
// Card union — for SSE compatibility
// ---------------------------------------------------------------------------

export type AnyCard =
  | Card
  | TaskCard
  | DecisionCard
  | VaultCard
  | ReplyCard
  | SystemCard
  | SuggestionCard

// ---------------------------------------------------------------------------
// Companion store
// ---------------------------------------------------------------------------

interface CompanionState {
  // Session
  session: SessionState | null
  sessions: PausedSession[]
  setSession: (session: SessionState | null) => void
  addPausedSession: (session: PausedSession) => void
  removePausedSession: (id: string) => void
  updateSessionTitle: (title: string) => void

  // Voice
  voiceState: VoiceState
  setVoiceState: (state: VoiceState) => void

  // Transcript (left column)
  segments: TranscriptSegment[]
  addSegment: (segment: TranscriptSegment) => void
  updateSegment: (id: string, text: string, isProvisional: boolean) => void
  clearSegments: () => void

  // Workspace — structured notes (right column top)
  noteGroups: NoteGroup[]
  addNoteGroup: (group: NoteGroup) => void
  updateNoteGroup: (id: string, changes: Partial<NoteGroup>) => void
  removeNoteGroup: (id: string) => void
  togglePinNoteGroup: (id: string) => void
  updateBullet: (groupId: string, bulletId: string, changes: Partial<NoteBullet>) => void
  removeBullet: (groupId: string, bulletId: string) => void
  clearNoteGroups: () => void

  // Approval queue (right column bottom)
  approvals: ApprovalItem[]
  addApproval: (item: ApprovalItem) => void
  updateApproval: (id: string, changes: Partial<ApprovalItem>) => void
  removeApproval: (id: string) => void
  clearApprovals: () => void

  // Thinking strip
  thinkingText: string | null
  setThinkingText: (text: string | null) => void

  // Focus anchor
  focusAnchor: FocusAnchor | null
  setFocusAnchor: (anchor: FocusAnchor | null) => void

  // Legacy: cards (for SSE compat — mapped to approvals)
  cards: AnyCard[]
  addCard: (card: AnyCard) => void
  removeCard: (id: string) => void
  updateCard: (id: string, changes: Partial<Card>) => void
  clearCards: () => void

  // Legacy: stream
  stream: StreamItem[]
  addStreamItem: (item: StreamItem) => void
  clearStream: () => void

  // Legacy: context cards
  contextCards: ContextCardData[]
  addContextCard: (card: ContextCardData) => void
  removeContextCard: (id: string) => void
  clearContextCards: () => void

  // Briefing
  briefing: Briefing | null
  setBriefing: (briefing: Briefing | null) => void

  // Session persistence
  sessionId: string | null
  setSessionId: (id: string | null) => void
  lastEventSequence: number
  setLastEventSequence: (seq: number) => void

  // Transcript collapse
  transcriptCollapsed: boolean
  setTranscriptCollapsed: (collapsed: boolean) => void

  // Clear all session data (for clean starts)
  clearSessionData: () => void
}

const MAX_STREAM = 500
const MAX_SEGMENTS = 200
const MAX_SESSIONS = 3

/** Merge entity tag arrays, deduplicating by name (case-insensitive). */
function mergeEntityTags(existing: EntityTag[], incoming: EntityTag[]): EntityTag[] {
  if (!incoming.length) return existing
  const seen = new Set(existing.map((t) => t.name.toLowerCase()))
  const merged = [...existing]
  for (const tag of incoming) {
    if (!seen.has(tag.name.toLowerCase())) {
      merged.push(tag)
      seen.add(tag.name.toLowerCase())
    }
  }
  return merged
}

export const useCompanionStore = create<CompanionState>()(
  persist(
    (set) => ({
      // Session
      session: null,
      sessions: [],
      setSession: (session) => set({ session }),
      addPausedSession: (session) =>
        set((s) => ({
          sessions: [session, ...s.sessions].slice(0, MAX_SESSIONS),
        })),
      removePausedSession: (id) =>
        set((s) => ({
          sessions: s.sessions.filter((sess) => sess.id !== id),
        })),
      updateSessionTitle: (title) =>
        set((s) => ({
          session: s.session ? { ...s.session, title } : null,
        })),

      // Voice
      voiceState: 'idle' as VoiceState,
      setVoiceState: (voiceState) => set({ voiceState }),

      // Segments
      segments: [],
      addSegment: (segment) =>
        set((s) => {
          if (s.segments.some((seg) => seg.id === segment.id)) return s
          const next = [...s.segments, segment]
          if (next.length > MAX_SEGMENTS) next.splice(0, next.length - MAX_SEGMENTS)
          return { segments: next }
        }),
      updateSegment: (id, text, isProvisional) =>
        set((s) => ({
          segments: s.segments.map((seg) =>
            seg.id === id ? { ...seg, text, isProvisional } : seg,
          ),
        })),
      clearSegments: () => set({ segments: [] }),

      // Note groups
      noteGroups: [],
      addNoteGroup: (group) =>
        set((s) => {
          const existing = s.noteGroups.find((g) => g.id === group.id)
          if (existing) {
            // Merge: add new bullets that don't already exist (dedup by text)
            const existingTexts = new Set(existing.bullets.map((b) => b.text.toLowerCase()))
            const newBullets = group.bullets.filter(
              (b) => !existingTexts.has(b.text.toLowerCase()),
            )
            if (newBullets.length === 0 && !group.entityTags?.length) return s
            return {
              noteGroups: s.noteGroups.map((g) =>
                g.id === group.id
                  ? {
                      ...g,
                      bullets: [...g.bullets, ...newBullets],
                      entityTags: mergeEntityTags(g.entityTags, group.entityTags ?? []),
                      timestamp: group.timestamp ?? g.timestamp,
                    }
                  : g,
              ),
            }
          }
          return { noteGroups: [...s.noteGroups, group] }
        }),
      updateNoteGroup: (id, changes) =>
        set((s) => ({
          noteGroups: s.noteGroups.map((g) =>
            g.id === id ? { ...g, ...changes } : g,
          ),
        })),
      removeNoteGroup: (id) =>
        set((s) => ({
          noteGroups: s.noteGroups.filter((g) => g.id !== id),
        })),
      togglePinNoteGroup: (id) =>
        set((s) => ({
          noteGroups: s.noteGroups.map((g) =>
            g.id === id ? { ...g, isPinned: !g.isPinned } : g,
          ),
        })),
      updateBullet: (groupId, bulletId, changes) =>
        set((s) => ({
          noteGroups: s.noteGroups.map((g) =>
            g.id === groupId
              ? {
                  ...g,
                  bullets: g.bullets.map((b) =>
                    b.id === bulletId ? { ...b, ...changes } : b,
                  ),
                }
              : g,
          ),
        })),
      removeBullet: (groupId, bulletId) =>
        set((s) => ({
          noteGroups: s.noteGroups.map((g) =>
            g.id === groupId
              ? { ...g, bullets: g.bullets.filter((b) => b.id !== bulletId) }
              : g,
          ),
        })),
      clearNoteGroups: () => set({ noteGroups: [] }),

      // Approvals
      approvals: [],
      addApproval: (item) =>
        set((s) => {
          if (s.approvals.some((a) => a.id === item.id)) return s
          return { approvals: [item, ...s.approvals] }
        }),
      updateApproval: (id, changes) =>
        set((s) => ({
          approvals: s.approvals.map((a) =>
            a.id === id ? { ...a, ...changes } : a,
          ),
        })),
      removeApproval: (id) =>
        set((s) => ({
          approvals: s.approvals.filter((a) => a.id !== id),
        })),
      clearApprovals: () => set({ approvals: [] }),

      // Thinking strip
      thinkingText: null,
      setThinkingText: (thinkingText) => set({ thinkingText }),

      // Focus anchor
      focusAnchor: null,
      setFocusAnchor: (focusAnchor) => set({ focusAnchor }),

      // Legacy: Cards (mapped to approvals for backward compat)
      cards: [],
      addCard: (card) =>
        set((s) => {
          if (s.cards.some((c) => c.id === card.id)) return s
          return { cards: [card, ...s.cards] }
        }),
      removeCard: (id) =>
        set((s) => ({ cards: s.cards.filter((c) => c.id !== id) })),
      updateCard: (id, changes) =>
        set((s) => ({
          cards: s.cards.map((c) => (c.id === id ? { ...c, ...changes } : c)),
        })),
      clearCards: () => set({ cards: [] }),

      // Legacy: Stream
      stream: [],
      addStreamItem: (item) =>
        set((s) => {
          const next = [...s.stream, item]
          if (next.length > MAX_STREAM) next.splice(0, next.length - MAX_STREAM)
          return { stream: next }
        }),
      clearStream: () => set({ stream: [] }),

      // Legacy: Context cards
      contextCards: [],
      addContextCard: (card) =>
        set((s) => {
          const existing = s.contextCards.findIndex((c) => c.id === card.id)
          if (existing >= 0) {
            const next = [...s.contextCards]
            next[existing] = card
            return { contextCards: next }
          }
          return { contextCards: [card, ...s.contextCards] }
        }),
      removeContextCard: (id) =>
        set((s) => ({
          contextCards: s.contextCards.filter((c) => c.id !== id),
        })),
      clearContextCards: () => set({ contextCards: [] }),

      // Briefing
      briefing: null,
      setBriefing: (briefing) => set({ briefing }),

      // Session persistence
      sessionId: null,
      setSessionId: (id) => set({ sessionId: id }),
      lastEventSequence: 0,
      setLastEventSequence: (seq) => set({ lastEventSequence: seq }),

      // Transcript collapse
      transcriptCollapsed: false,
      setTranscriptCollapsed: (transcriptCollapsed) => set({ transcriptCollapsed }),

      // Clear all session data for a fresh start
      clearSessionData: () =>
        set({
          segments: [],
          noteGroups: [],
          approvals: [],
          cards: [],
          stream: [],
          contextCards: [],
          thinkingText: null,
          focusAnchor: null,
          voiceState: 'idle' as VoiceState,
        }),
    }),
    {
      name: 'qareen-companion',
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({
        voiceState: state.voiceState,
        cards: state.cards,
        stream: state.stream,
        segments: state.segments,
        contextCards: state.contextCards,
        briefing: state.briefing,
        sessionId: state.sessionId,
        lastEventSequence: state.lastEventSequence,
        session: state.session,
        sessions: state.sessions,
        noteGroups: state.noteGroups,
        approvals: state.approvals,
        transcriptCollapsed: state.transcriptCollapsed,
      }),
    },
  ),
)
