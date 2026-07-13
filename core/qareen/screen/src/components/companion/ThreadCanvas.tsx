import { useState, memo } from 'react'
import {
  Lightbulb,
  CheckSquare,
  Scale,
  HelpCircle,
  ListOrdered,
  MessageCircle,
  Heart,
  ChevronDown,
  ChevronRight,
} from 'lucide-react'
import { format } from 'date-fns'

// ---------------------------------------------------------------------------
// ThreadCanvas — renders conversation threads as collapsible note groups.
//
// Main visual for Focus mode. Each thread is a glass card containing
// ThoughtUnits classified as idea, task, decision, question, plan, context,
// or emotion. Active thread has a pulsing left border accent.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ThoughtUnit {
  id: string
  threadId: string
  text: string
  speaker: string
  timestamp: string
  classification: 'idea' | 'task' | 'decision' | 'question' | 'plan' | 'context' | 'emotion'
  confidence: number
  entities: string[]
}

export interface Thread {
  id: string
  title: string
  summary: string
  units: ThoughtUnit[]
  isActive: boolean
  firstSeen: string
  lastSeen: string
}

interface ThreadCanvasProps {
  threads: Thread[]
  activeThreadId: string | null
}

// ---------------------------------------------------------------------------
// Classification → icon map
// ---------------------------------------------------------------------------

const CLASSIFICATION_ICONS: Record<ThoughtUnit['classification'], typeof Lightbulb> = {
  idea: Lightbulb,
  task: CheckSquare,
  decision: Scale,
  question: HelpCircle,
  plan: ListOrdered,
  context: MessageCircle,
  emotion: Heart,
}

const CLASSIFICATION_COLORS: Record<ThoughtUnit['classification'], string> = {
  idea: 'text-yellow',
  task: 'text-blue',
  decision: 'text-accent',
  question: 'text-purple',
  plan: 'text-teal',
  context: 'text-text-quaternary',
  emotion: 'text-red',
}

// ---------------------------------------------------------------------------
// ThreadCanvas
// ---------------------------------------------------------------------------

export const ThreadCanvas = memo(function ThreadCanvas({
  threads,
  activeThreadId,
}: ThreadCanvasProps) {
  if (threads.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center px-6">
        <p className="text-[13px] font-serif text-text-quaternary text-center leading-relaxed">
          Start speaking — threads will appear as you talk
        </p>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto space-y-2 px-3 py-3">
      {threads.map((thread) => (
        <ThreadCard
          key={thread.id}
          thread={thread}
          isActive={thread.id === activeThreadId}
        />
      ))}

      <style>{`
        @keyframes thread-pulse {
          0%, 100% { opacity: 0.6; }
          50% { opacity: 1; }
        }
        @keyframes unit-in {
          from { opacity: 0; transform: translateY(4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  )
})

// ---------------------------------------------------------------------------
// ThreadCard — collapsible thread container
// ---------------------------------------------------------------------------

const ThreadCard = memo(function ThreadCard({
  thread,
  isActive,
}: {
  thread: Thread
  isActive: boolean
}) {
  const [collapsed, setCollapsed] = useState(false)

  const lastSeenFormatted = (() => {
    try {
      return format(new Date(thread.lastSeen), 'HH:mm')
    } catch {
      return ''
    }
  })()

  return (
    <div
      className={`
        rounded-[5px] overflow-hidden
        bg-[var(--glass-bg)] backdrop-blur-[12px]
        border border-[var(--glass-border)]
        transition-all duration-[220ms]
        ${isActive ? 'border-l-2 border-l-accent' : ''}
      `}
      style={isActive ? { borderLeftWidth: 2, borderLeftColor: 'var(--accent)', animation: 'thread-pulse 2400ms ease-in-out infinite' } : undefined}
    >
      {/* Header */}
      <button
        onClick={() => setCollapsed((prev) => !prev)}
        className="
          w-full flex items-center gap-2 px-3 py-2.5
          cursor-pointer
          hover:bg-hover/50 transition-colors duration-[80ms]
        "
      >
        {collapsed ? (
          <ChevronRight className="w-3 h-3 text-text-quaternary shrink-0" />
        ) : (
          <ChevronDown className="w-3 h-3 text-text-quaternary shrink-0" />
        )}

        <span className="text-[13px] font-[520] text-text truncate flex-1 text-left">
          {thread.title}
        </span>

        {/* Unit count badge */}
        <span className="
          text-[11px] font-[510] text-text-tertiary
          bg-[rgba(255,245,235,0.04)] px-1.5 py-0.5 rounded-full
          shrink-0
        ">
          {thread.units.length}
        </span>

        {/* Timestamp */}
        {lastSeenFormatted && (
          <span className="text-[11px] text-text-quaternary font-mono tabular-nums shrink-0">
            {lastSeenFormatted}
          </span>
        )}
      </button>

      {/* Summary — shown when collapsed and summary exists */}
      {collapsed && thread.summary && (
        <div className="px-3 pb-2.5 -mt-0.5">
          <p className="text-[12px] font-serif text-text-tertiary leading-relaxed truncate">
            {thread.summary}
          </p>
        </div>
      )}

      {/* Units */}
      {!collapsed && thread.units.length > 0 && (
        <div className="border-t border-border px-3 py-2 space-y-1.5">
          {thread.units.map((unit, idx) => (
            <UnitRow key={unit.id} unit={unit} index={idx} />
          ))}
        </div>
      )}
    </div>
  )
})

// ---------------------------------------------------------------------------
// UnitRow — single thought unit inside a thread
// ---------------------------------------------------------------------------

function UnitRow({ unit, index }: { unit: ThoughtUnit; index: number }) {
  const Icon = CLASSIFICATION_ICONS[unit.classification]
  const iconColor = CLASSIFICATION_COLORS[unit.classification]

  return (
    <div
      className="flex items-start gap-2 group/unit"
      style={{
        animation: 'unit-in 220ms ease-out both',
        animationDelay: `${index * 40}ms`,
      }}
    >
      {/* Classification icon */}
      <Icon className={`w-3 h-3 mt-[3px] shrink-0 ${iconColor}`} />

      {/* Text + entities */}
      <div className="flex-1 min-w-0">
        <p className="text-[12px] font-serif text-text-secondary leading-relaxed">
          {unit.text}
        </p>

        {/* Entity tags */}
        {unit.entities.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {unit.entities.map((entity) => (
              <span
                key={entity}
                className="
                  text-[11px] font-[450] text-text-tertiary
                  bg-[rgba(255,245,235,0.04)] px-1.5 py-0.5 rounded-full
                "
              >
                {entity}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
