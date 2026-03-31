import { FileText } from 'lucide-react'
import type { VaultCard } from '@/lib/types'
import { Tag } from '@/components/primitives'
import { CardShell } from './CardShell'

interface VaultCardViewProps {
  card: VaultCard
  onApprove: (id: string) => void
  onDismiss: (id: string) => void
  onEdit: (id: string) => void
  isFocused?: boolean
}

export function VaultCardView({
  card,
  onApprove,
  onDismiss,
  onEdit,
  isFocused,
}: VaultCardViewProps) {
  return (
    <CardShell
      card={card}
      isFocused={isFocused}
      onApprove={onApprove}
      onDismiss={onDismiss}
      onEdit={onEdit}
      typeBadge={
        <span className="inline-flex items-center gap-1.5 px-2 h-5 rounded-xs text-[11px] font-medium bg-green-muted text-green">
          <FileText className="w-3 h-3" />
          Vault Note
        </span>
      }
    >
      <div className="space-y-2">
        {/* Note type + path */}
        <div className="flex items-center gap-2">
          <Tag label={card.note_type} color="green" size="sm" />
          {card.suggested_path && (
            <span className="type-caption text-text-quaternary font-mono truncate">
              {card.suggested_path}
            </span>
          )}
        </div>

        {/* Tags */}
        {card.tags.length > 0 && (
          <div className="flex items-center flex-wrap gap-1.5">
            {card.tags.map((tag) => (
              <Tag key={tag} label={tag} color="gray" size="sm" />
            ))}
          </div>
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
