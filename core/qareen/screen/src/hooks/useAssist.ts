/**
 * useAssist — Orchestrates the quick-assist flow:
 *   text → POST /api/assist → execute action → return result
 */

import { useState, useCallback } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import { api } from '@/lib/api'
import { usePageActions, serializeActions } from './usePageActions'
import { useActionExecutor, type AssistResponse, type ExecutionResult } from './useActionExecutor'
import { useQareenContext } from '@/store/qareenContext'

// Reuse the same page context logic from FloatingAgent
function getPageContext(pathname: string, searchParams: URLSearchParams): { label: string; detail: string | null } | null {
  if (pathname === '/work' || pathname.startsWith('/work')) {
    const tab = searchParams.get('tab')
    const tabLabels: Record<string, string> = { today: 'Today', tasks: 'Tasks', projects: 'Projects', goals: 'Goals' }
    return { label: 'Work', detail: tab ? tabLabels[tab] ?? tab : 'Today' }
  }
  if (pathname.startsWith('/vault')) {
    if (pathname.includes('/knowledge')) return { label: 'Vault', detail: 'Knowledge' }
    if (pathname.includes('/logs')) return { label: 'Vault', detail: 'Logs' }
    return { label: 'Vault', detail: null }
  }
  if (pathname === '/timeline' || pathname.startsWith('/timeline')) {
    return { label: 'Timeline', detail: null }
  }
  if (pathname === '/settings') {
    const section = searchParams.get('section')
    return { label: 'Settings', detail: section ? section.charAt(0).toUpperCase() + section.slice(1) : null }
  }
  if (pathname === '/people') return { label: 'People', detail: null }
  if (pathname === '/system') return { label: 'System', detail: null }
  if (pathname === '/agents') return { label: 'Agents', detail: null }
  if (pathname === '/automations') return { label: 'Automations', detail: null }
  if (pathname === '/chat') return { label: 'Chat', detail: null }
  if (pathname === '/') return { label: 'Companion', detail: null }
  return null
}

interface AssistResult {
  response: AssistResponse
  execution: ExecutionResult
}

export function useAssist() {
  const [isPending, setIsPending] = useState(false)
  const [lastResult, setLastResult] = useState<AssistResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const location = useLocation()
  const [searchParams] = useSearchParams()
  const actions = usePageActions()
  const { execute } = useActionExecutor()
  const focus = useQareenContext(s => s.focus)
  const activeTopics = useQareenContext(s => s.activeTopics)
  const recentActions = useQareenContext(s => s.recentActions)

  const assist = useCallback(async (input: string): Promise<AssistResult | null> => {
    if (!input.trim()) return null
    setIsPending(true)
    setError(null)

    try {
      const pageCtx = getPageContext(location.pathname, searchParams)
      const specs = serializeActions(actions)

      const response = await api.post<AssistResponse>('/assist', {
        input: input.trim(),
        page: pageCtx?.label ?? 'unknown',
        page_detail: pageCtx?.detail ?? null,
        actions: specs,
        context: {
          focus,
          active_topics: activeTopics.slice(0, 3),
          recent_actions: recentActions.slice(-3).map(a => a.spoken),
        },
      })

      const execution = await execute(response)
      const result = { response, execution }
      setLastResult(result)
      return result
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Assist request failed'
      setError(msg)
      setLastResult(null)
      return null
    } finally {
      setIsPending(false)
    }
  }, [location.pathname, searchParams, actions, execute])

  return { assist, isPending, lastResult, error }
}
