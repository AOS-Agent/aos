// ---------------------------------------------------------------------------
// One-time migration — legacy chat localStorage → SQLite conversations.
//
// Runs on first mount after Phase 2 ships. Reads the old `qareen-chat-sessions`
// localStorage key, POSTs to /api/conversations/import, and marks the
// migration complete via a sentinel key so it doesn't run twice.
//
// Idempotent: safe to call multiple times. If the sentinel is present or
// there's nothing to migrate, it's a no-op.
// ---------------------------------------------------------------------------

const LEGACY_KEY = 'qareen-chat-sessions'
const MIGRATED_KEY = 'qareen-chat-migrated-v1'

interface LegacyMessage {
  id: string
  role: 'user' | 'assistant' | 'tool' | 'system'
  text: string
  source?: string
  ts: number
  toolName?: string
  toolPreview?: string
  isError?: boolean
  durationMs?: number
  costUsd?: number
}

interface LegacySession {
  id: string
  name: string
  createdAt: number
  messages: LegacyMessage[]
}

interface ImportMessage {
  role: string
  text: string
  ts: number
  source?: string
  tool_name?: string
  tool_preview?: string
  is_error?: boolean
  duration_ms?: number
  cost_usd?: number
}

interface ImportConversation {
  title?: string
  archived: boolean
  capabilities: Record<string, boolean>
  metadata: Record<string, unknown>
  messages: ImportMessage[]
}

export async function migrateLegacyChatIfNeeded(): Promise<void> {
  try {
    if (typeof window === 'undefined') return
    if (localStorage.getItem(MIGRATED_KEY)) return

    const raw = localStorage.getItem(LEGACY_KEY)
    if (!raw) {
      // Nothing to migrate — mark done anyway so we don't keep checking
      localStorage.setItem(MIGRATED_KEY, new Date().toISOString())
      return
    }

    const legacy = JSON.parse(raw) as { sessions?: LegacySession[] } | LegacySession[]
    const sessions: LegacySession[] = Array.isArray(legacy)
      ? legacy
      : legacy?.sessions ?? []

    if (!Array.isArray(sessions) || sessions.length === 0) {
      localStorage.setItem(MIGRATED_KEY, new Date().toISOString())
      return
    }

    // Only migrate sessions that actually have messages
    const nonEmpty = sessions.filter(s => Array.isArray(s.messages) && s.messages.length > 0)
    if (nonEmpty.length === 0) {
      localStorage.setItem(MIGRATED_KEY, new Date().toISOString())
      return
    }

    const payload = {
      conversations: nonEmpty.map<ImportConversation>((s) => ({
        title: s.name || 'Chat',
        archived: true,  // legacy sessions are historical
        capabilities: {
          response: true,
          voice: false,
          notes: false,
          approvals: false,
          research: false,
          threads: false,
        },
        metadata: {
          legacy_chat_session_id: s.id,
          legacy_created_at: s.createdAt,
        },
        messages: s.messages.map<ImportMessage>((m) => ({
          role: m.role,
          text: m.text,
          ts: m.ts,
          source: m.source,
          tool_name: m.toolName,
          tool_preview: m.toolPreview,
          is_error: m.isError,
          duration_ms: m.durationMs,
          cost_usd: m.costUsd,
        })),
      })),
    }

    const res = await fetch('/api/conversations/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })

    if (!res.ok) {
      console.warn('[migrateLegacyChat] Import failed:', res.status)
      return  // Don't mark migrated — we'll retry next session
    }

    const result = await res.json()
    console.log(`[migrateLegacyChat] Imported ${result.imported ?? 0} conversations`)

    localStorage.setItem(MIGRATED_KEY, new Date().toISOString())
    // Keep the legacy key for now as a safety net; a future version can remove it.
  } catch (err) {
    console.warn('[migrateLegacyChat] Error:', err)
    // Don't mark migrated — retry on next load
  }
}
