import { AlertTriangle, Info, AlertCircle, Flame } from 'lucide-react'
import type { SystemCard } from '@/lib/types'
import { CardShell } from './CardShell'

const severityConfig: Record<
  string,
  { icon: typeof Info; badge: string; label: string }
> = {
  info: {
    icon: Info,
    badge: 'bg-blue-muted text-blue',
    label: 'Info',
  },
  warning: {
    icon: AlertTriangle,
    badge: 'bg-yellow-muted text-yellow',
    label: 'Warning',
  },
  error: {
    icon: AlertCircle,
    badge: 'bg-red-muted text-red',
    label: 'Error',
  },
  critical: {
    icon: Flame,
    badge: 'bg-red-muted text-red',
    label: 'Critical',
  },
}

interface SystemCardViewProps {
  card: SystemCard
  onApprove: (id: string) => void
  onDismiss: (id: string) => void
  onEdit: (id: string) => void
  isFocused?: boolean
}

export function SystemCardView({
  card,
  onApprove,
  onDismiss,
  onEdit,
  isFocused,
}: SystemCardViewProps) {
  const config = severityConfig[card.severity] ?? severityConfig.info
  const Icon = config.icon

  return (
    <CardShell
      card={card}
      isFocused={isFocused}
      onApprove={onApprove}
      onDismiss={onDismiss}
      onEdit={onEdit}
      typeBadge={
        <span
          className={`inline-flex items-center gap-1.5 px-2 h-5 rounded-xs text-[11px] font-medium ${config.badge}`}
        >
          <Icon className="w-3 h-3" />
          {config.label}
        </span>
      }
    >
      <div className="space-y-2">
        {/* Service name */}
        <div className="flex items-center gap-2">
          <span className="type-caption text-text-quaternary">Service:</span>
          <span className="type-label text-text-secondary font-mono text-[12px]">
            {card.service_name}
          </span>
        </div>

        {/* Body */}
        {card.body && (
          <p className="type-body text-text-secondary leading-relaxed">
            {card.body}
          </p>
        )}

        {/* Suggested action */}
        {card.suggested_action && (
          <div className="bg-bg-tertiary rounded-xs px-2.5 py-2">
            <p className="type-caption text-text-tertiary">
              <span className="text-text-secondary font-medium">Suggested: </span>
              {card.suggested_action}
            </p>
          </div>
        )}
      </div>
    </CardShell>
  )
}
