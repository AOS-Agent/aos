import { create } from 'zustand'
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
// Stream item union — everything that flows through the center column
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
// Context card (left column)
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
// Companion store
// ---------------------------------------------------------------------------

export type AnyCard =
  | Card
  | TaskCard
  | DecisionCard
  | VaultCard
  | ReplyCard
  | SystemCard
  | SuggestionCard

interface CompanionState {
  // Voice
  voiceState: VoiceState
  setVoiceState: (state: VoiceState) => void

  // Approval queue
  cards: AnyCard[]
  addCard: (card: AnyCard) => void
  removeCard: (id: string) => void
  updateCard: (id: string, changes: Partial<Card>) => void
  clearCards: () => void

  // Stream
  stream: StreamItem[]
  addStreamItem: (item: StreamItem) => void
  clearStream: () => void

  // Transcript
  segments: TranscriptSegment[]
  addSegment: (segment: TranscriptSegment) => void
  updateSegment: (id: string, text: string, isProvisional: boolean) => void
  clearSegments: () => void

  // Context
  contextCards: ContextCardData[]
  addContextCard: (card: ContextCardData) => void
  removeContextCard: (id: string) => void
  clearContextCards: () => void

  // Briefing
  briefing: Briefing | null
  setBriefing: (briefing: Briefing | null) => void
}

const MAX_STREAM = 500
const MAX_SEGMENTS = 200

export const useCompanionStore = create<CompanionState>((set) => ({
  // Voice
  voiceState: 'idle',
  setVoiceState: (voiceState) => set({ voiceState }),

  // Cards
  cards: [],
  addCard: (card) =>
    set((s) => {
      // Deduplicate: skip if a card with the same ID already exists
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

  // Stream
  stream: [],
  addStreamItem: (item) =>
    set((s) => {
      const next = [...s.stream, item]
      if (next.length > MAX_STREAM) next.splice(0, next.length - MAX_STREAM)
      return { stream: next }
    }),
  clearStream: () => set({ stream: [] }),

  // Transcript
  segments: [],
  addSegment: (segment) =>
    set((s) => {
      // Deduplicate: skip if a segment with the same ID already exists
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

  // Context
  contextCards: [],
  addContextCard: (card) =>
    set((s) => {
      // Replace existing card with same id, or prepend
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
}))
