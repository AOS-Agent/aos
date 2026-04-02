import { useState, useCallback, useRef, memo } from 'react'
import {
  Pin,
  PinOff,
  CircleDot,
  CheckCircle2,
  Lightbulb,
  Gavel,
  User,
  FolderOpen,
  Hash,
} from 'lucide-react'
import { useCompanionStore } from '@/store/companion'
import type { NoteGroup as NoteGroupType, NoteBullet, EntityTag } from '@/store/companion'
import { format } from 'date-fns'

// ---------------------------------------------------------------------------
// NoteGroup — a structured note group in the workspace.
//
// Title (bold), bullet list (each editable on click), entity tags at bottom,
// pin toggle, timestamp.
// ---------------------------------------------------------------------------

const BULLET_ICONS: Record<NoteBullet['type'], typeof CircleDot> = {
  note: CircleDot,
  action: CheckCircle2,
  decision: Gavel,
  insight: Lightbulb,
}

const BULLET_COLORS: Record<NoteBullet['type'], string> = {
  note: 'text-text-quaternary',
  action: 'text-blue',
  decision: 'text-accent',
  insight: 'text-purple',
}

const ENTITY_ICONS: Record<EntityTag['type'], typeof User> = {
  person: User,
  project: FolderOpen,
  topic: Hash,
}

const ENTITY_COLORS: Record<EntityTag['type'], string> = {
  person: 'text-blue bg-blue-muted',
  project: 'text-purple bg-purple-muted',
  topic: 'text-teal bg-teal-muted',
}

interface NoteGroupProps {
  group: NoteGroupType
}

export const NoteGroupComponent = memo(function NoteGroupComponent({ group }: NoteGroupProps) {
  const togglePin = useCompanionStore((s) => s.togglePinNoteGroup)
  const updateBullet = useCompanionStore((s) => s.updateBullet)
  const [isHovered, setIsHovered] = useState(false)

  const time = (() => {
    try {
      return format(new Date(group.timestamp), 'HH:mm')
    } catch {
      return ''
    }
  })()

  // Sort: pinned items have visual indicator, but order is maintained by store
  return (
    <div
      className={`
        px-4 py-3 border-b border-border
        hover:bg-hover/50
        transition-colors duration-[var(--duration-instant)]
        animate-[notegroup-in_220ms_ease-out]
        ${group.isPinned ? 'bg-accent-subtle/30' : ''}
      `}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Header: title + time + pin */}
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <h3 className="type-label text-text leading-snug flex-1 min-w-0">
          {group.title}
        </h3>
        <div className="flex items-center gap-1 shrink-0">
          {time && (
            <span className="type-tiny text-text-quaternary">{time}</span>
          )}
          {(isHovered || group.isPinned) && (
            <button
              onClick={() => togglePin(group.id)}
              className={`
                h-5 w-5 rounded-xs inline-flex items-center justify-center
                transition-all duration-[var(--duration-instant)]
                ${group.isPinned
                  ? 'text-accent hover:text-accent-hover'
                  : 'text-text-quaternary hover:text-text-tertiary'
                }
              `}
              title={group.isPinned ? 'Unpin' : 'Pin'}
            >
              {group.isPinned ? (
                <PinOff className="w-3 h-3" />
              ) : (
                <Pin className="w-3 h-3" />
              )}
            </button>
          )}
        </div>
      </div>

      {/* Bullets */}
      <div className="space-y-1 ml-0.5">
        {group.bullets.map((bullet) => (
          <BulletItem
            key={bullet.id}
            bullet={bullet}
            groupId={group.id}
            onUpdate={updateBullet}
          />
        ))}
      </div>

      {/* Entity tags */}
      {group.entityTags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2.5">
          {group.entityTags.map((tag) => (
            <EntityTagChip key={tag.id} tag={tag} />
          ))}
        </div>
      )}

      <style>{`
        @keyframes notegroup-in {
          from { opacity: 0; transform: translateY(4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  )
})

// ---------------------------------------------------------------------------
// BulletItem — editable bullet point
// ---------------------------------------------------------------------------

interface BulletItemProps {
  bullet: NoteBullet
  groupId: string
  onUpdate: (groupId: string, bulletId: string, changes: Partial<NoteBullet>) => void
}

function BulletItem({ bullet, groupId, onUpdate }: BulletItemProps) {
  const [isEditing, setIsEditing] = useState(false)
  const [editValue, setEditValue] = useState(bullet.text)
  const inputRef = useRef<HTMLInputElement>(null)

  const Icon = BULLET_ICONS[bullet.type]
  const iconColor = BULLET_COLORS[bullet.type]

  const startEditing = useCallback(() => {
    setEditValue(bullet.text)
    setIsEditing(true)
    setTimeout(() => inputRef.current?.focus(), 0)
  }, [bullet.text])

  const commitEdit = useCallback(() => {
    const trimmed = editValue.trim()
    if (trimmed && trimmed !== bullet.text) {
      onUpdate(groupId, bullet.id, { text: trimmed })
    }
    setIsEditing(false)
  }, [editValue, bullet.text, bullet.id, groupId, onUpdate])

  return (
    <div className="flex items-start gap-1.5 group/bullet">
      <Icon className={`w-3 h-3 mt-[3px] shrink-0 ${iconColor}`} />
      {isEditing ? (
        <input
          ref={inputRef}
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          onBlur={commitEdit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitEdit()
            if (e.key === 'Escape') setIsEditing(false)
          }}
          className="
            flex-1 min-w-0 h-5 px-1
            bg-transparent border-b border-accent/30
            text-[12px] text-text-secondary
            focus:outline-none
          "
        />
      ) : (
        <p
          onClick={startEditing}
          className="
            flex-1 min-w-0 type-caption text-text-secondary leading-relaxed
            cursor-text hover:text-text transition-colors
          "
        >
          {bullet.text}
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// EntityTagChip — inline entity tag
// ---------------------------------------------------------------------------

function EntityTagChip({ tag }: { tag: EntityTag }) {
  const Icon = ENTITY_ICONS[tag.type] ?? Hash
  const color = ENTITY_COLORS[tag.type] ?? 'text-text-tertiary bg-bg-tertiary'

  return (
    <span
      className={`
        inline-flex items-center gap-1 px-1.5 h-5 rounded-xs
        text-[10px] font-medium
        ${color}
        cursor-default
      `}
    >
      <Icon className="w-2.5 h-2.5" />
      {tag.name}
    </span>
  )
}
