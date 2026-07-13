/**
 * usePageActions — Page capability registry for the quick-assist system.
 *
 * Pages declare what they can do via useRegisterPageActions().
 * The FloatingAgent reads registered actions via usePageActions().
 * Actions are tied to component lifecycle — unmount = gone.
 */

import { createContext, useContext, useCallback, useRef, useState, useEffect, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useUIStore } from '@/store/ui'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ActionParam {
  name: string
  type: 'string' | 'number' | 'boolean' | 'enum'
  required: boolean
  description: string
  options?: string[]
}

export interface PageAction {
  id: string
  label: string
  category: 'navigate' | 'mutate' | 'filter' | 'toggle' | 'create' | 'search'
  params?: ActionParam[]
  execute: (params: Record<string, unknown>) => void | Promise<void>
}

/** Serializable subset sent to the model (no execute fn) */
export interface ActionSpec {
  id: string
  label: string
  category: string
  params?: Omit<ActionParam, 'description'>[]
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

interface PageActionsContextValue {
  actions: PageAction[]
  register: (actions: PageAction[]) => () => void
}

const PageActionsContext = createContext<PageActionsContextValue | null>(null)

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function PageActionsProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const toggleSidebar = useUIStore(s => s.toggleSidebar)
  const toggleCommandPalette = useUIStore(s => s.toggleCommandPalette)

  // Mutable ref for fast updates; state for re-renders
  const registryRef = useRef<Map<string, PageAction[]>>(new Map())
  const [actions, setActions] = useState<PageAction[]>([])

  // Stable refs for action callbacks — avoids recreating globalActions on every render
  const navRef = useRef(navigate)
  const sidebarRef = useRef(toggleSidebar)
  const paletteRef = useRef(toggleCommandPalette)
  navRef.current = navigate
  sidebarRef.current = toggleSidebar
  paletteRef.current = toggleCommandPalette

  // Global actions — always available regardless of page
  const globalActions = useRef<PageAction[]>([
    { id: 'nav.work', label: 'Go to Work', category: 'navigate', execute: () => navRef.current('/work') },
    { id: 'nav.work_today', label: 'Go to Today view', category: 'navigate', execute: () => navRef.current('/work?tab=today') },
    { id: 'nav.work_tasks', label: 'Go to Tasks', category: 'navigate', execute: () => navRef.current('/work?tab=tasks') },
    { id: 'nav.people', label: 'Go to People', category: 'navigate', execute: () => navRef.current('/people') },
    { id: 'nav.vault', label: 'Go to Vault', category: 'navigate', execute: () => navRef.current('/vault') },
    { id: 'nav.system', label: 'Go to System', category: 'navigate', execute: () => navRef.current('/system') },
    { id: 'nav.settings', label: 'Go to Settings', category: 'navigate', execute: () => navRef.current('/settings') },
    { id: 'nav.timeline', label: 'Go to Timeline', category: 'navigate', execute: () => navRef.current('/timeline') },
    { id: 'nav.agents', label: 'Go to Agents', category: 'navigate', execute: () => navRef.current('/agents') },
    { id: 'nav.automations', label: 'Go to Automations', category: 'navigate', execute: () => navRef.current('/automations') },
    { id: 'nav.chat', label: 'Go to Chat', category: 'navigate', execute: () => navRef.current('/chat') },
    { id: 'nav.companion', label: 'Go to Companion', category: 'navigate', execute: () => navRef.current('/') },
    { id: 'ui.sidebar', label: 'Toggle sidebar', category: 'toggle', execute: () => sidebarRef.current() },
    { id: 'ui.command_palette', label: 'Open command palette', category: 'toggle', execute: () => paletteRef.current() },
    { id: 'ui.theme', label: 'Toggle theme', category: 'toggle', execute: () => {
      const html = document.documentElement
      html.setAttribute('data-theme', html.getAttribute('data-theme') === 'light' ? 'dark' : 'light')
    }},
  ])

  const rebuild = useCallback(() => {
    const all = [...globalActions.current]
    for (const pageActions of registryRef.current.values()) {
      all.push(...pageActions)
    }
    setActions(all)
  }, [])

  const register = useCallback((pageActions: PageAction[]) => {
    const key = crypto.randomUUID()
    registryRef.current.set(key, pageActions)
    rebuild()
    return () => {
      registryRef.current.delete(key)
      rebuild()
    }
  }, [rebuild])

  // Build initial global actions — runs once
  useEffect(() => { rebuild() }, [rebuild])

  return (
    <PageActionsContext.Provider value={{ actions, register }}>
      {children}
    </PageActionsContext.Provider>
  )
}

// ---------------------------------------------------------------------------
// Consumer hooks
// ---------------------------------------------------------------------------

/** Read all registered actions (global + page-specific) */
export function usePageActions(): PageAction[] {
  const ctx = useContext(PageActionsContext)
  if (!ctx) throw new Error('usePageActions must be inside PageActionsProvider')
  return ctx.actions
}

/** Serialize actions to the compact spec format sent to the model */
export function serializeActions(actions: PageAction[]): ActionSpec[] {
  return actions.map(a => ({
    id: a.id,
    label: a.label,
    category: a.category,
    params: a.params?.map(p => ({
      name: p.name,
      type: p.type,
      required: p.required,
      options: p.options,
    })),
  }))
}

/** Register page-specific actions. Call in page component body. */
export function useRegisterPageActions(actions: PageAction[]) {
  const ctx = useContext(PageActionsContext)
  if (!ctx) throw new Error('useRegisterPageActions must be inside PageActionsProvider')

  const actionsRef = useRef(actions)
  actionsRef.current = actions

  useEffect(() => {
    return ctx.register(actionsRef.current)
  }, [ctx])
}
