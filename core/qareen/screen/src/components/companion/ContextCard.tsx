import { useRef, useEffect, memo } from 'react'
import { User, FolderOpen, Hash, Calendar } from 'lucide-react'
import type { ContextCardData, ContextCardType } from '@/store/companion'

// ---------------------------------------------------------------------------
// ContextCard — a reactive context item in the left column.
//
// Types: person, project, topic, schedule.
// Animate in with translateY(-8px) → 0, opacity 0 → 1, 180ms.
// ---------------------------------------------------------------------------

const iconMap: Record<ContextCardType, typeof User> = {
  person: User,
  project: FolderOpen,
  topic: Hash,
  schedule: Calendar,
}

const colorMap: Record<ContextCardType, string> = {
  person: 'text-blue',
  project: 'text-purple',
  topic: 'text-teal',
  schedule: 'text-orange',
}

interface ContextCardProps {
  card: ContextCardData
}

export const ContextCard = memo(function ContextCard({ card }: ContextCardProps) {
  const ref = useRef<HTMLDivElement>(null)
  const mounted = useRef(false)

  useEffect(() => {
    const el = ref.current
    if (!el || mounted.current) return
    mounted.current = true
    el.style.opacity = '0'
    el.style.transform = 'translateY(-8px)'
    requestAnimationFrame(() => {
      el.style.transition =
        'opacity 180ms var(--ease-out), transform 180ms var(--ease-out)'
      el.style.opacity = '1'
      el.style.transform = 'translateY(0)'
    })
  }, [])

  const Icon = iconMap[card.type] ?? Hash
  const iconColor = colorMap[card.type] ?? 'text-text-tertiary'
  const data = card.data

  return (
    <div
      ref={ref}
      className="px-3 py-2.5 border-b border-border last:border-b-0 hover:bg-hover transition-colors duration-[var(--duration-instant)]"
    >
      {/* Header: icon + title */}
      <div className="flex items-center gap-2 mb-1">
        <Icon className={`w-3.5 h-3.5 ${iconColor} shrink-0`} />
        <span className="type-label text-text truncate">{card.title}</span>
      </div>

      {/* Subtitle */}
      {card.subtitle && (
        <p className="type-caption text-text-tertiary mb-1 pl-[22px]">
          {card.subtitle}
        </p>
      )}

      {/* Type-specific details */}
      {card.type === 'person' && <PersonDetails data={data} />}
      {card.type === 'project' && <ProjectDetails data={data} />}
      {card.type === 'topic' && <TopicDetails data={data} />}
      {card.type === 'schedule' && <ScheduleDetails data={data} />}
    </div>
  )
})

// --- Person ---
function PersonDetails({ data }: { data: Record<string, unknown> }) {
  const lastContact = data.last_contact as string | undefined
  const openItems = data.open_items as number | undefined
  const trend = data.trend as string | undefined

  return (
    <div className="pl-[22px] space-y-0.5">
      {lastContact && (
        <p className="type-tiny text-text-quaternary">
          Last: {lastContact}
        </p>
      )}
      {openItems !== undefined && (
        <p className="type-tiny text-text-quaternary">
          {openItems} open item{openItems !== 1 ? 's' : ''}
        </p>
      )}
      {trend && (
        <p className="type-tiny text-text-quaternary">{trend}</p>
      )}
      {data.recent_message ? (
        <p className="type-tiny text-text-quaternary italic truncate">
          &quot;{String(data.recent_message)}&quot;
        </p>
      ) : null}
    </div>
  )
}

// --- Project ---
function ProjectDetails({ data }: { data: Record<string, unknown> }) {
  const progress = data.progress as number | undefined
  const activeTasks = data.active_tasks as number | undefined
  const recentDecisions = data.recent_decisions as string[] | undefined

  return (
    <div className="pl-[22px] space-y-0.5">
      {progress !== undefined && (
        <div className="flex items-center gap-2">
          <div className="flex-1 h-1 bg-bg-tertiary rounded-full overflow-hidden">
            <div
              className="h-full bg-accent rounded-full"
              style={{ width: `${Math.min(100, progress)}%` }}
            />
          </div>
          <span className="type-tiny text-text-quaternary">{progress}%</span>
        </div>
      )}
      {activeTasks !== undefined && (
        <p className="type-tiny text-text-quaternary">
          {activeTasks} active task{activeTasks !== 1 ? 's' : ''}
        </p>
      )}
      {recentDecisions && recentDecisions.length > 0 && (
        <p className="type-tiny text-text-quaternary truncate">
          Recent: {recentDecisions[0]}
        </p>
      )}
    </div>
  )
}

// --- Topic ---
function TopicDetails({ data }: { data: Record<string, unknown> }) {
  const notes = data.vault_notes as string[] | undefined
  const decisions = data.related_decisions as string[] | undefined

  return (
    <div className="pl-[22px] space-y-0.5">
      {notes && notes.length > 0 && (
        <p className="type-tiny text-text-quaternary">
          {notes.length} related note{notes.length !== 1 ? 's' : ''}
        </p>
      )}
      {decisions && decisions.length > 0 && (
        <p className="type-tiny text-text-quaternary truncate">
          Decision: {decisions[0]}
        </p>
      )}
    </div>
  )
}

// --- Schedule ---
function ScheduleDetails({ data }: { data: Record<string, unknown> }) {
  const items = data.items as string[] | undefined

  return (
    <div className="pl-[22px] space-y-0.5">
      {items?.map((item, i) => (
        <p key={i} className="type-tiny text-text-quaternary">
          {item}
        </p>
      ))}
    </div>
  )
}
