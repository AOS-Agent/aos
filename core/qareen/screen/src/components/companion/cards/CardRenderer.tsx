import { CardType } from '@/lib/types'
import type {
  Card,
  TaskCard,
  DecisionCard,
  VaultCard,
  ReplyCard,
  SystemCard,
  SuggestionCard,
} from '@/lib/types'
import { TaskCardView } from './TaskCardView'
import { DecisionCardView } from './DecisionCardView'
import { VaultCardView } from './VaultCardView'
import { ReplyCardView } from './ReplyCardView'
import { SystemCardView } from './SystemCardView'
import { SuggestionCardView } from './SuggestionCardView'

interface CardRendererProps {
  card: Card
  onApprove: (id: string) => void
  onDismiss: (id: string) => void
  onEdit: (id: string) => void
  isFocused?: boolean
}

export function CardRenderer({
  card,
  onApprove,
  onDismiss,
  onEdit,
  isFocused,
}: CardRendererProps) {
  const common = { onApprove, onDismiss, onEdit, isFocused }

  switch (card.card_type) {
    case CardType.TASK:
      return <TaskCardView card={card as TaskCard} {...common} />
    case CardType.DECISION:
      return <DecisionCardView card={card as DecisionCard} {...common} />
    case CardType.VAULT:
      return <VaultCardView card={card as VaultCard} {...common} />
    case CardType.REPLY:
      return <ReplyCardView card={card as ReplyCard} {...common} />
    case CardType.SYSTEM:
      return <SystemCardView card={card as SystemCard} {...common} />
    case CardType.SUGGESTION:
      return <SuggestionCardView card={card as SuggestionCard} {...common} />
    default:
      return null
  }
}
