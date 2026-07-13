import {
  MessageCircle,
  Users,
  Map,
  Inbox,
} from 'lucide-react'

// ---------------------------------------------------------------------------
// OrbitalPills — 4 mode pills arranged beneath the Qareen orb.
//
// Talk · Meet · Plan · Clear
// ---------------------------------------------------------------------------

export interface PillMode {
  id: string
  label: string
  icon: React.ComponentType<{ className?: string }>
  skill: string
}

export const MODES: PillMode[] = [
  { id: 'talk',  label: 'Talk',  icon: MessageCircle, skill: 'thinking' },
  { id: 'meet',  label: 'Meet',  icon: Users,         skill: 'meeting' },
  { id: 'plan',  label: 'Plan',  icon: Map,           skill: 'planning' },
  { id: 'clear', label: 'Clear', icon: Inbox,          skill: 'email' },
]

const PILL_SIZE = 44
const COL_WIDTH = 72 // fixed width per column so all align

interface OrbitalPillsProps {
  onSelect?: (mode: PillMode) => void
  hoveredPill?: string | null
  onHoverChange?: (id: string | null) => void
  radiusX?: number
}

export function OrbitalPills({
  onSelect,
  hoveredPill,
  onHoverChange,
}: OrbitalPillsProps) {
  return (
    <div className="flex items-start justify-center gap-1">
      {MODES.map((mode) => {
        const Icon = mode.icon
        const isHovered = hoveredPill === mode.id
        const otherHovered = hoveredPill != null && !isHovered

        return (
          <div
            key={mode.id}
            className="flex flex-col items-center"
            style={{ width: COL_WIDTH }}
          >
            {/* Circular pill */}
            <button
              onMouseEnter={() => onHoverChange?.(mode.id)}
              onMouseLeave={() => onHoverChange?.(null)}
              onClick={() => onSelect?.(mode)}
              className={`
                flex items-center justify-center
                rounded-full
                backdrop-blur-md
                border
                cursor-pointer
                transition-all ease-[cubic-bezier(0.34,1.56,0.64,1)]
                ${isHovered
                  ? `duration-200 bg-[rgba(217,115,13,0.18)] border-[rgba(217,115,13,0.4)] text-white
                     shadow-[0_0_24px_rgba(217,115,13,0.3),0_0_8px_rgba(217,115,13,0.15)]
                     scale-110`
                  : otherHovered
                    ? 'duration-300 bg-[rgba(21,18,16,0.4)] border-[rgba(255,245,235,0.04)] text-text-quaternary opacity-40 scale-95'
                    : 'duration-300 bg-[rgba(21,18,16,0.5)] border-[rgba(255,245,235,0.08)] text-text-secondary'
                }
              `}
              style={{ width: PILL_SIZE, height: PILL_SIZE }}
            >
              <Icon className={`transition-all duration-200 ${isHovered ? 'w-[18px] h-[18px]' : 'w-4 h-4'}`} />
            </button>

            {/* Label — always visible */}
            <span
              className={`
                mt-1.5 text-[10px] font-medium tracking-wider uppercase
                whitespace-nowrap transition-colors duration-200
                ${isHovered
                  ? 'text-text-secondary'
                  : otherHovered
                    ? 'text-text-quaternary/30'
                    : 'text-text-quaternary/60'
                }
              `}
            >
              {mode.label}
            </span>
          </div>
        )
      })}
    </div>
  )
}
