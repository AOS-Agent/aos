import { useState, useMemo, Suspense } from 'react'
import { ElevenLabsOrb, type AgentState } from '@/components/ui/elevenlabs-orb'

// ---------------------------------------------------------------------------
// Orb — the Qareen presence.
//
// WebGL 3D orb. Voice reactivity via agentState prop:
//   'listening' → dramatic shader animation (rings, deformation, speed)
//   null        → gentle idle breathing
// Colors shift with prayer periods.
// ---------------------------------------------------------------------------

export type OrbState = 'idle' | 'listening' | 'speaking' | 'processing'

interface OrbProps {
  size?: number
  fill?: boolean
  state?: OrbState
  colors?: [string, string]
  className?: string
  onClick?: () => void
  micConnected?: boolean
}

function mapState(state: OrbState): AgentState {
  switch (state) {
    case 'idle': return null
    case 'listening': return 'listening'
    case 'speaking': return 'talking'
    case 'processing': return 'thinking'
  }
}

export function Orb({
  size = 280,
  fill = false,
  state = 'idle',
  colors,
  className,
  onClick,
  micConnected = false,
}: OrbProps) {
  const [hovered, setHovered] = useState(false)
  const agentState = useMemo(() => mapState(state), [state])

  const defaultColors: [string, string] = micConnected
    ? ['#D9730D', '#E8943D']
    : ['#8B5A2B', '#A0744B']

  const activeColors = hovered
    ? (['#E8943D', '#F5C27A'] as [string, string])
    : (colors ?? defaultColors)

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={onClick}
      className={`${fill ? 'w-full h-full' : ''} transition-transform duration-300 ease-out ${onClick ? 'cursor-pointer' : ''} ${hovered && onClick ? 'scale-[1.04]' : ''} ${className ?? ''}`}
      style={fill ? undefined : { width: size, height: size }}
    >
      <Suspense fallback={<div className="w-full h-full" />}>
        <ElevenLabsOrb
          colors={activeColors}
          agentState={agentState}
          seed={42}
          className="w-full h-full"
        />
      </Suspense>
    </div>
  )
}
