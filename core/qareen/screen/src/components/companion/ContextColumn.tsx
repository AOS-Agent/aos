import { Sparkles } from 'lucide-react'
import { useCompanionStore } from '@/store/companion'
import { ContextCard } from './ContextCard'

// ---------------------------------------------------------------------------
// ContextColumn — left sidebar (280px, hidden on mobile).
//
// Driven by SSE `context` events. Shows person, project, topic, and schedule
// cards as they become relevant to the conversation.
// ---------------------------------------------------------------------------

export function ContextColumn() {
  const contextCards = useCompanionStore((s) => s.contextCards)

  return (
    <div className="h-full flex flex-col bg-bg-panel">
      {/* Header */}
      <div className="px-3 py-3 border-b border-border">
        <span className="type-overline text-text-quaternary">Context</span>
      </div>

      {/* Cards */}
      <div className="flex-1 overflow-y-auto">
        {contextCards.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full px-4">
            <Sparkles className="w-6 h-6 text-text-quaternary opacity-20 mb-2" />
            <p className="type-caption text-text-quaternary text-center">
              Context appears as you talk
            </p>
          </div>
        ) : (
          contextCards.map((card) => (
            <ContextCard key={card.id} card={card} />
          ))
        )}
      </div>
    </div>
  )
}
