import { useCompanionStore } from '@/store/companion'

// ---------------------------------------------------------------------------
// ThinkingStrip — minimal text that appears/disappears at the bottom
// of the workspace area. No container, no borders, just text.
// Uses text-text-quaternary in Inter. Fades in/out smoothly.
// ---------------------------------------------------------------------------

export function ThinkingStrip() {
  const thinkingText = useCompanionStore((s) => s.thinkingText)

  if (!thinkingText) return null

  return (
    <div className="shrink-0 px-5 py-1.5 overflow-hidden">
      <div className="flex items-center gap-2 animate-[thinking-in_180ms_ease-out] min-w-0">
        {/* Animated dots */}
        <div className="flex items-center gap-[3px] shrink-0">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="w-[3px] h-[3px] rounded-full bg-text-quaternary"
              style={{
                animation: 'thinking-pulse 1200ms ease-in-out infinite',
                animationDelay: `${i * 200}ms`,
              }}
            />
          ))}
        </div>
        <p className="text-[11px] font-[450] text-text-quaternary truncate">
          {thinkingText}
        </p>
      </div>

      <style>{`
        @keyframes thinking-in {
          from { opacity: 0; transform: translateY(2px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes thinking-pulse {
          0%, 100% { opacity: 0.3; }
          50% { opacity: 0.8; }
        }
      `}</style>
    </div>
  )
}
