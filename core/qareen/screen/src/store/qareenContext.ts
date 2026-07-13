// ---------------------------------------------------------------------------
// Qareen Context Store — persistent shared state across all surfaces
//
// Companion sessions write (focus, decisions, entities).
// Quick Assist and other pages read.
// Backed by the /api/context endpoints which persist to JSON on disk.
// ---------------------------------------------------------------------------

import { create } from 'zustand'
import { api } from '@/lib/api'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Decision {
  text: string
  thread: string
  timestamp: string
}

export interface ActionEntry {
  input: string
  action_id: string
  spoken: string
  page: string
  timestamp: string
}

export interface Entity {
  name: string
  type: string
  last_mentioned: string
}

// ---------------------------------------------------------------------------
// State shape
// ---------------------------------------------------------------------------

export interface QareenContextState {
  // Data (mirrors backend QareenContext)
  focus: string | null
  activeTopics: string[]
  recentDecisions: Decision[]
  recentActions: ActionEntry[]
  recentEntities: Entity[]
  pageHistory: string[]
  activeSessionId: string | null
  learning: Record<string, number>

  // Meta
  loaded: boolean

  // Actions
  hydrate: () => Promise<void>
  setFocus: (focus: string | null) => Promise<void>
  addAction: (action: ActionEntry) => Promise<void>
  addPageVisit: (page: string) => Promise<void>
  recordApproval: (classification: string) => Promise<void>
  recordDismissal: (classification: string) => Promise<void>
}

// ---------------------------------------------------------------------------
// Backend response shape (snake_case from Python)
// ---------------------------------------------------------------------------

interface ContextResponse {
  focus: string | null
  active_topics: string[]
  recent_decisions: Decision[]
  recent_actions: ActionEntry[]
  recent_entities: Entity[]
  page_history: string[]
  active_session_id: string | null
  learning: Record<string, number>
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useQareenContext = create<QareenContextState>((set) => ({
  // Initial state
  focus: null,
  activeTopics: [],
  recentDecisions: [],
  recentActions: [],
  recentEntities: [],
  pageHistory: [],
  activeSessionId: null,
  learning: {},
  loaded: false,

  hydrate: async () => {
    try {
      const data = await api.get<ContextResponse>('/context')
      set({
        focus: data.focus,
        activeTopics: data.active_topics ?? [],
        recentDecisions: data.recent_decisions ?? [],
        recentActions: data.recent_actions ?? [],
        recentEntities: data.recent_entities ?? [],
        pageHistory: data.page_history ?? [],
        activeSessionId: data.active_session_id,
        learning: data.learning ?? {},
        loaded: true,
      })
    } catch (err) {
      console.warn('[qareen-context] hydrate failed:', err)
      set({ loaded: true })
    }
  },

  setFocus: async (focus) => {
    set({ focus })
    api.post('/context/focus', { focus }).catch((err) => {
      console.warn('[qareen-context] setFocus failed:', err)
    })
  },

  addAction: async (action) => {
    set((s) => ({
      recentActions: [...s.recentActions, action].slice(-20),
    }))
    api.post('/context/action', action).catch((err) => {
      console.warn('[qareen-context] addAction failed:', err)
    })
  },

  addPageVisit: async (page) => {
    if (!page) return
    set((s) => ({
      pageHistory: [...s.pageHistory, page].slice(-10),
    }))
    api.post('/context/page', { page }).catch((err) => {
      console.warn('[qareen-context] addPageVisit failed:', err)
    })
  },

  recordApproval: async (classification) => {
    if (!classification) return
    set((s) => ({
      learning: {
        ...s.learning,
        [classification]: Math.max(0.30, (s.learning[classification] ?? 0.70) - 0.02),
      },
    }))
    api.post('/context/approval', { classification }).catch((err) => {
      console.warn('[qareen-context] recordApproval failed:', err)
    })
  },

  recordDismissal: async (classification) => {
    if (!classification) return
    set((s) => ({
      learning: {
        ...s.learning,
        [classification]: Math.min(0.95, (s.learning[classification] ?? 0.70) + 0.03),
      },
    }))
    api.post('/context/dismissal', { classification }).catch((err) => {
      console.warn('[qareen-context] recordDismissal failed:', err)
    })
  },
}))
