import { useIntelTiers } from '@/hooks/usePeople';
import { Skeleton } from '@/components/primitives/Skeleton';

// ---------------------------------------------------------------------------
// TierStrip — horizontal tier badges with aggregate counts, click-to-filter
// ---------------------------------------------------------------------------

interface TierStripProps {
  selectedTier: string | null;
  onSelectTier: (tier: string | null) => void;
}

const TIER_ORDER = ['core', 'active', 'emerging', 'fading', 'dormant', 'unknown'] as const;

// Resolve tailwind classes per tier — kept explicit so the JIT picks them up
const TIER_STYLE: Record<string, { text: string; bg: string }> = {
  core: { text: 'text-tag-red', bg: 'bg-tag-red-bg' },
  active: { text: 'text-tag-green', bg: 'bg-tag-green-bg' },
  emerging: { text: 'text-tag-blue', bg: 'bg-tag-blue-bg' },
  fading: { text: 'text-tag-orange', bg: 'bg-tag-orange-bg' },
  dormant: { text: 'text-tag-gray', bg: 'bg-tag-gray-bg' },
  unknown: { text: 'text-tag-gray', bg: 'bg-tag-gray-bg' },
};

export function TierStrip({ selectedTier, onSelectTier }: TierStripProps) {
  const { data, isLoading, isError } = useIntelTiers();

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 mb-6">
        {TIER_ORDER.map(t => (
          <Skeleton key={t} className="h-6 w-20 rounded-xs" />
        ))}
      </div>
    );
  }

  if (isError || !data) return null;

  const tiers = data.tiers || {};
  const total = Object.values(tiers).reduce((sum, n) => sum + (n || 0), 0);
  if (total === 0) return null;

  return (
    <div className="flex items-center gap-2 mb-6 flex-wrap">
      {TIER_ORDER.map(tier => {
        const count = tiers[tier] ?? 0;
        if (count === 0 && tier !== selectedTier) return null;
        const active = selectedTier === tier;
        const style = TIER_STYLE[tier];
        const muted = tier === 'unknown' ? 'opacity-60' : '';
        return (
          <button
            key={tier}
            onClick={() => onSelectTier(active ? null : tier)}
            className={`
              inline-flex items-center gap-1.5 h-6 px-2 rounded-xs
              text-[11px] font-medium leading-[1.2] cursor-pointer
              transition-colors ${style.text} ${style.bg} ${muted}
              ${active ? 'ring-1 ring-accent' : 'hover:brightness-125'}
            `}
            style={{ transitionDuration: 'var(--duration-instant)' }}
            aria-pressed={active}
          >
            <span className="capitalize">{tier}</span>
            <span className="tabular-nums opacity-80">{count}</span>
          </button>
        );
      })}
      {selectedTier && (
        <button
          onClick={() => onSelectTier(null)}
          className="text-[10px] text-text-quaternary hover:text-text-tertiary px-2 h-6 cursor-pointer transition-colors"
          style={{ transitionDuration: 'var(--duration-instant)' }}
        >
          Clear
        </button>
      )}
    </div>
  );
}

export default TierStrip;
