import { Lightbulb } from 'lucide-react'
import type { SuggestionCard } from '@/lib/types'
import { Tag } from '@/components/primitives'
import { CardShell } from './CardShell'

interface SuggestionCardViewProps {
  card: SuggestionCard
  onApprove: (id: string) => void
  onDismiss: (id: string) => void
  onEdit: (id: string) => void
  isFocused?: boolean
}

export function SuggestionCardView({
  card,
  onApprove,
  onDismiss,
  onEdit,
  isFocused,
}: SuggestionCardViewProps) {
  return (
    <CardShell
      card={card}
      isFocused={isFocused}
      onApprove={onApprove}
      onDismiss={onDismiss}
      onEdit={onEdit}
      typeBadge={
        <span className="inline-flex items-center gap-1.5 px-2 h-5 rounded-xs text-[11px] font-medium bg-orange-muted text-orange">
          <Lightbulb className="w-3 h-3" />
          Suggestion
        </span>
      }
    >
      <div className="space-y-2">
        {/* Pattern name */}
        <div className="flex items-center gap-2">
          <Tag label={card.pattern} color="orange" size="sm" />
        </div>

        {/* Observation */}
        <p className="type-body text-text-secondary leading-relaxed">
          {card.observation}
        </p>

        {/* Suggested actions */}
        {card.suggested_actions.length > 0 && (
          <div className="space-y-1">
            {card.suggested_actions.map((action, i) => (
              <div
                key={i}
                className="
                  bg-bg-tertiary rounded-xs px-2.5 py-1.5
                  type-caption text-text-tertiary
                  hover:bg-hover cursor-pointer
                  transition-colors duration-[var(--duration-instant)]
                "
              >
                {action}
              </div>
            ))}
          </div>
        )}

        {/* Related entities */}
        {card.related_entities.length > 0 && (
          <div className="flex items-center flex-wrap gap-1.5">
            {card.related_entities.map((entity) => (
              <Tag key={entity} label={entity} color="gray" size="sm" />
            ))}
          </div>
        )}
      </div>
    </CardShell>
  )
}
