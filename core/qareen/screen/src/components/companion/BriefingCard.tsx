import { Sun, Moon, Calendar, AlertCircle, BarChart3 } from 'lucide-react'
import type { Briefing } from '@/store/companion'
import { useOperator } from '@/hooks/useConfig'

// ---------------------------------------------------------------------------
// BriefingCard — morning or evening briefing in the stream.
//
// Shows: summary, today's schedule, attention items, metrics.
// Styled as a card with accent-muted bg and subtle left border.
// ---------------------------------------------------------------------------

interface BriefingCardProps {
  briefing: Briefing
}

export function BriefingCard({ briefing }: BriefingCardProps) {
  const { data: op } = useOperator()
  const displayName = op?.nickname || op?.name?.split(' ')[0]
  const hour = new Date(briefing.timestamp).getHours()
  const isMorning = hour < 12
  const Icon = isMorning ? Sun : Moon

  return (
    <div className="mx-4 my-2 bg-accent-muted border-l-2 border-accent rounded-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 pt-3 pb-1">
        <Icon className="w-4 h-4 text-accent" />
        <span className="type-label text-text">
          {isMorning ? `Good morning${displayName ? ', ' + displayName : ''}` : 'Evening recap'}
        </span>
      </div>

      {/* Summary */}
      {briefing.summary && (
        <div className="px-3 pb-2">
          <p className="type-body text-text-secondary leading-relaxed">
            {briefing.summary}
          </p>
        </div>
      )}

      {/* Schedule */}
      {briefing.schedule.length > 0 && (
        <div className="px-3 pb-2">
          <div className="flex items-center gap-1.5 mb-1">
            <Calendar className="w-3 h-3 text-text-tertiary" />
            <span className="type-tiny text-text-tertiary uppercase tracking-wider">
              Schedule
            </span>
          </div>
          <div className="space-y-0.5">
            {briefing.schedule.map((item, i) => (
              <p key={i} className="type-caption text-text-secondary">
                {item}
              </p>
            ))}
          </div>
        </div>
      )}

      {/* Attention items */}
      {briefing.attention.length > 0 && (
        <div className="px-3 pb-2">
          <div className="flex items-center gap-1.5 mb-1">
            <AlertCircle className="w-3 h-3 text-yellow" />
            <span className="type-tiny text-text-tertiary uppercase tracking-wider">
              Needs attention
            </span>
          </div>
          <div className="space-y-0.5">
            {briefing.attention.map((item, i) => (
              <p key={i} className="type-caption text-text-secondary">
                {item}
              </p>
            ))}
          </div>
        </div>
      )}

      {/* Metrics */}
      {Object.keys(briefing.metrics).length > 0 && (
        <div className="px-3 pb-3">
          <div className="flex items-center gap-1.5 mb-1">
            <BarChart3 className="w-3 h-3 text-text-tertiary" />
            <span className="type-tiny text-text-tertiary uppercase tracking-wider">
              Metrics
            </span>
          </div>
          <div className="flex flex-wrap gap-3">
            {Object.entries(briefing.metrics).map(([key, value]) => (
              <div key={key} className="flex items-baseline gap-1">
                <span className="type-label text-text">{value}</span>
                <span className="type-tiny text-text-quaternary">{key}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
