import { Scale } from 'lucide-react'
import type { DecisionCard } from '@/lib/types'
import { Tag } from '@/components/primitives'
import { CardShell } from './CardShell'

interface DecisionCardViewProps {
  card: DecisionCard
  onApprove: (id: string) => void
  onDismiss: (id: string) => void
  onEdit: (id: string) => void
  isFocused?: boolean
}

export function DecisionCardView({
  card,
  onApprove,
  onDismiss,
  onEdit,
  isFocused,
}: DecisionCardViewProps) {
  return (
    <CardShell
      card={card}
      isFocused={isFocused}
      onApprove={onApprove}
      onDismiss={onDismiss}
      onEdit={onEdit}
      typeBadge={
        <span className="inline-flex items-center gap-1.5 px-2 h-5 rounded-xs text-[11px] font-medium bg-purple-muted text-purple">
          <Scale className="w-3 h-3" />
          Decision
        </span>
      }
    >
      <div className="space-y-2">
        {/* Rationale */}
        <p className="type-body text-text-secondary leading-relaxed">
          {card.rationale}
        </p>

        {/* Stakeholders */}
        {card.stakeholders.length > 0 && (
          <div className="flex items-center flex-wrap gap-1.5">
            {card.stakeholders.map((name) => (
              <Tag key={name} label={name} color="teal" size="sm" />
            ))}
          </div>
        )}

        {/* Project */}
        {card.project && (
          <Tag label={card.project} color="purple" size="sm" />
        )}

        {/* Body */}
        {card.body && (
          <p className="type-caption text-text-tertiary leading-relaxed">
            {card.body}
          </p>
        )}
      </div>
    </CardShell>
  )
}
