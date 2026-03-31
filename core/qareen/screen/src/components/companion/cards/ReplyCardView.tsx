import { MessageSquare, Mail, Send } from 'lucide-react'
import type { ReplyCard } from '@/lib/types'
import { CardShell } from './CardShell'

const channelIcon: Record<string, typeof MessageSquare> = {
  telegram: Send,
  whatsapp: MessageSquare,
  email: Mail,
  slack: MessageSquare,
  sms: MessageSquare,
}

const channelColor: Record<string, string> = {
  telegram: 'bg-blue-muted text-blue',
  whatsapp: 'bg-green-muted text-green',
  email: 'bg-orange-muted text-orange',
  slack: 'bg-purple-muted text-purple',
  sms: 'bg-teal-muted text-teal',
}

interface ReplyCardViewProps {
  card: ReplyCard
  onApprove: (id: string) => void
  onDismiss: (id: string) => void
  onEdit: (id: string) => void
  isFocused?: boolean
}

export function ReplyCardView({
  card,
  onApprove,
  onDismiss,
  onEdit,
  isFocused,
}: ReplyCardViewProps) {
  const Icon = channelIcon[card.channel] ?? MessageSquare
  const badgeColor = channelColor[card.channel] ?? 'bg-tag-gray-bg text-tag-gray'

  return (
    <CardShell
      card={card}
      isFocused={isFocused}
      onApprove={onApprove}
      onDismiss={onDismiss}
      onEdit={onEdit}
      typeBadge={
        <span
          className={`inline-flex items-center gap-1.5 px-2 h-5 rounded-xs text-[11px] font-medium ${badgeColor}`}
        >
          <Icon className="w-3 h-3" />
          Reply
        </span>
      }
    >
      <div className="space-y-2">
        {/* Recipient */}
        <div className="flex items-center gap-2">
          <span className="type-caption text-text-tertiary">To:</span>
          <span className="type-label text-text-secondary">
            {card.recipient}
          </span>
          <span className="type-tiny text-text-quaternary capitalize">
            via {card.channel}
          </span>
        </div>

        {/* Draft text */}
        <div className="bg-bg-tertiary rounded-xs p-2.5">
          <p className="type-body text-text-secondary leading-relaxed whitespace-pre-wrap">
            {card.draft_text}
          </p>
        </div>

        {/* Body (any extra context) */}
        {card.body && (
          <p className="type-caption text-text-tertiary leading-relaxed">
            {card.body}
          </p>
        )}
      </div>
    </CardShell>
  )
}
