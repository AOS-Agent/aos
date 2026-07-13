/**
 * useActionExecutor — Maps model response to a registered page action and executes it.
 */

import { useCallback } from 'react'
import { usePageActions, type PageAction } from './usePageActions'

export interface AssistResponse {
  action_id: string | null
  params: Record<string, unknown>
  spoken: string
  confidence: number
}

export interface ExecutionResult {
  success: boolean
  message: string
  action?: PageAction
}

export function useActionExecutor() {
  const actions = usePageActions()

  const execute = useCallback(async (response: AssistResponse): Promise<ExecutionResult> => {
    if (!response.action_id) {
      return { success: false, message: response.spoken }
    }

    const action = actions.find(a => a.id === response.action_id)
    if (!action) {
      return { success: false, message: `Action "${response.action_id}" not available on this page.` }
    }

    try {
      await action.execute(response.params)
      return { success: true, message: response.spoken, action }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Action failed'
      return { success: false, message: msg }
    }
  }, [actions])

  return { execute }
}
