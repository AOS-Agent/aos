import { useState, useMemo } from 'react';
import { Users, ChevronDown, ChevronUp } from 'lucide-react';
import { useCircles, useCircleDetail } from '@/hooks/usePeople';
import { Tag, type TagColor } from '@/components/primitives/Tag';
import { SectionHeader } from '@/components/primitives/SectionHeader';
import { EmptyState } from '@/components/primitives/EmptyState';
import { Skeleton, SkeletonRows } from '@/components/primitives/Skeleton';
import type { CircleResponse, CircleMemberResponse } from '@/lib/types';

// ---------------------------------------------------------------------------
// Category color mapping
// ---------------------------------------------------------------------------

const CATEGORY_COLORS: Record<string, TagColor> = {
  family: 'orange',
  work: 'blue',
  community: 'purple',
  friends: 'green',
  religious: 'teal',
};

function categoryColor(cat?: string): TagColor {
  if (!cat) return 'gray';
  return CATEGORY_COLORS[cat.toLowerCase()] || 'gray';
}

const FILTER_TABS = ['All', 'Family', 'Work', 'Community', 'Friends'] as const;

// ---------------------------------------------------------------------------
// Confidence bar
// ---------------------------------------------------------------------------

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-12 h-1.5 rounded-full bg-bg-tertiary overflow-hidden">
        <div
          className="h-full rounded-full bg-accent"
          style={{ width: `${pct}%`, transitionDuration: 'var(--duration-fast)' }}
        />
      </div>
      <span className="text-[10px] text-text-quaternary tabular-nums bg-bg-tertiary px-1.5 py-px rounded-full">{pct}%</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Initials avatar (local)
// ---------------------------------------------------------------------------

function MemberInitials({ name }: { name: string }) {
  const clean = name.replace(/[^\p{L}\p{N}\s]/gu, '').trim();
  const letters = clean.split(/\s+/).map(w => w[0] || '').join('').slice(0, 2).toUpperCase();
  return (
    <div className="w-7 h-7 rounded-full bg-bg-tertiary border border-border-secondary flex items-center justify-center text-[10px] font-[590] text-text-tertiary shrink-0">
      {letters || '?'}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Circle Card
// ---------------------------------------------------------------------------

function CircleCard({ circle, isExpanded, onToggle, onSelect }: {
  circle: CircleResponse;
  isExpanded: boolean;
  onToggle: () => void;
  onSelect: (personId: string) => void;
}) {
  const { data: detail, isLoading } = useCircleDetail(isExpanded ? circle.id : null);
  const members = detail?.members ?? [];

  return (
    <div
      className="bg-bg-secondary border border-border rounded-[7px] transition-colors"
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      <button
        onClick={onToggle}
        className="w-full text-left p-4 cursor-pointer"
      >
        <div className="flex items-start justify-between mb-2">
          <div className="flex-1 min-w-0">
            <h3 className="text-[13px] font-[510] text-text-secondary truncate">{circle.name}</h3>
            {circle.subcategory && (
              <span className="text-[11px] text-text-quaternary">{circle.subcategory}</span>
            )}
          </div>
          {isExpanded
            ? <ChevronUp className="w-3.5 h-3.5 text-text-quaternary shrink-0 mt-0.5" />
            : <ChevronDown className="w-3.5 h-3.5 text-text-quaternary shrink-0 mt-0.5" />
          }
        </div>
        <div className="flex items-center gap-2">
          <Tag label={circle.category || 'uncategorized'} color={categoryColor(circle.category)} size="sm" />
          <span className="text-[11px] text-text-quaternary">{circle.member_count} member{circle.member_count !== 1 ? 's' : ''}</span>
          <div className="ml-auto">
            <ConfidenceBar value={circle.confidence} />
          </div>
        </div>
      </button>

      {isExpanded && (
        <div className="border-t border-border px-4 pb-3 pt-2">
          {isLoading ? (
            <SkeletonRows count={3} />
          ) : members.length === 0 ? (
            <p className="text-[11px] text-text-quaternary py-2">No members found</p>
          ) : (
            <div className="space-y-px">
              {members.map((m: CircleMemberResponse) => (
                <button
                  key={m.person_id}
                  onClick={(e) => { e.stopPropagation(); onSelect(m.person_id); }}
                  className="group w-full text-left flex items-center gap-2.5 px-2 py-1.5 rounded-[5px] hover:bg-hover transition-colors cursor-pointer"
                  style={{ transitionDuration: 'var(--duration-instant)' }}
                >
                  <MemberInitials name={m.name} />
                  <div className="flex-1 min-w-0">
                    <span className="text-[12px] font-[510] text-text-secondary truncate block group-hover:text-text transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
                      {m.name}
                    </span>
                    {m.role_in_circle && (
                      <span className="text-[10px] text-text-quaternary">{m.role_in_circle}</span>
                    )}
                  </div>
                  <ConfidenceBar value={m.confidence} />
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// CircleBrowser
// ---------------------------------------------------------------------------

export default function CircleBrowser({ onSelect }: { onSelect: (personId: string) => void }) {
  const { data, isLoading } = useCircles();
  const [filter, setFilter] = useState('All');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const circles = data?.circles ?? [];

  const filtered = useMemo(() => {
    if (filter === 'All') return circles;
    return circles.filter(c => (c.category || '').toLowerCase() === filter.toLowerCase());
  }, [circles, filter]);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-7 w-64" />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {[1, 2, 3, 4].map(i => <Skeleton key={i} className="h-28 rounded-[7px]" />)}
        </div>
      </div>
    );
  }

  if (circles.length === 0) {
    return (
      <EmptyState
        icon={<Users />}
        title="No circles yet"
        description="Circles are automatically detected from your relationships and interactions."
      />
    );
  }

  return (
    <div>
      {/* Filter row — pill shaped */}
      <div className="flex items-center gap-2 mb-5">
        <div className="inline-flex items-center gap-1 rounded-full bg-bg-secondary/60 backdrop-blur-sm border border-border p-1">
          {FILTER_TABS.map(tab => (
            <button
              key={tab}
              onClick={() => setFilter(tab)}
              className={`px-3 py-1 rounded-full text-[11px] font-[510] transition-colors cursor-pointer ${
                filter === tab
                  ? 'bg-bg-tertiary text-text-secondary shadow-[0_0_0_1px_rgba(255,245,235,0.06)]'
                  : 'text-text-quaternary hover:text-text-tertiary'
              }`}
              style={{ transitionDuration: 'var(--duration-instant)' }}
            >
              {tab}
            </button>
          ))}
        </div>
        <span className="text-[10px] text-text-quaternary ml-auto tabular-nums">{filtered.length} circle{filtered.length !== 1 ? 's' : ''}</span>
      </div>

      {/* Card grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {filtered.map(circle => (
          <CircleCard
            key={circle.id}
            circle={circle}
            isExpanded={expandedId === circle.id}
            onToggle={() => setExpandedId(expandedId === circle.id ? null : circle.id)}
            onSelect={onSelect}
          />
        ))}
      </div>
    </div>
  );
}
