import { ListTodo } from 'lucide-react'
import type { TaskCard } from '@/lib/types'
import { Tag } from '@/components/primitives'
import { StatusDot } from '@/components/primitives'
import { CardShell } from './CardShell'

// Priority dot color mapping
const priorityColor: Record<number, 'red' | 'orange' | 'yellow' | 'blue' | 'gray'> = {
  1: 'red',
  2: 'orange',
  3: 'yellow',
  4: 'blue',
  5: 'gray',
}

const priorityLabel: Record<number, string> = {
  1: 'Critical',
  2: 'High',
  3: 'Normal',
  4: 'Low',
  5: 'Someday',
}

interface TaskCardViewProps {
  card: TaskCard
  onApprove: (id: string) => void
  onDismiss: (id: string) => void
  onEdit: (id: string) => void
  isFocused?: boolean
}

export function TaskCardView({
  card,
  onApprove,
  onDismiss,
  onEdit,
  isFocused,
}: TaskCardViewProps) {
  return (
    <CardShell
      card={card}
      isFocused={isFocused}
      onApprove={onApprove}
      onDismiss={onDismiss}
      onEdit={onEdit}
      typeBadge={
        <span className="inline-flex items-center gap-1.5 px-2 h-5 rounded-xs text-[11px] font-medium bg-blue-muted text-blue">
          <ListTodo className="w-3 h-3" />
          {card.is_update ? 'Update Task' : 'New Task'}
        </span>
      }
    >
      <div className="space-y-2">
        {/* Proposed task title */}
        <p className="type-body text-text-secondary">{card.task_title}</p>

        {/* Metadata row */}
        <div className="flex items-center flex-wrap gap-2">
          {card.task_project && (
            <Tag label={card.task_project} color="purple" size="sm" />
          )}
          <StatusDot
            color={priorityColor[card.task_priority] ?? 'gray'}
            size="sm"
            label={priorityLabel[card.task_priority] ?? 'Normal'}
          />
          {card.task_assignee && (
            <span className="type-caption text-text-tertiary">
              {card.task_assignee}
            </span>
          )}
          {card.task_due && (
            <span className="type-caption text-text-quaternary">
              Due {card.task_due}
            </span>
          )}
        </div>

        {/* Body (extra context) */}
        {card.body && (
          <p className="type-caption text-text-tertiary leading-relaxed">
            {card.body}
          </p>
        )}
      </div>
    </CardShell>
  )
}
