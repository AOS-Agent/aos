import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Rss } from 'lucide-react';
import { useIntelligenceFeed, useIntelligenceStats } from '@/hooks/useIntelligence';
import type { IntelligenceItem } from '@/hooks/useIntelligence';
import { Tag, type TagColor } from '@/components/primitives/Tag';
import { StatusDot } from '@/components/primitives/StatusDot';
import { EmptyState } from '@/components/primitives/EmptyState';
import { ErrorBanner } from '@/components/primitives/ErrorBanner';
import { Skeleton } from '@/components/primitives/Skeleton';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function timeAgo(iso: string | undefined | null): string {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  if (days < 30) return `${Math.floor(days / 7)}w ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

const platformTagColor: Record<string, TagColor> = {
  twitter: 'blue',
  youtube: 'red',
  github: 'gray',
  hackernews: 'orange',
  blog: 'purple',
  arxiv: 'green',
  reddit: 'orange',
  rss: 'teal',
};

const PLATFORMS = ['All', 'Twitter', 'YouTube', 'GitHub', 'HN', 'Blogs', 'arXiv'] as const;

const platformFilterMap: Record<string, string | undefined> = {
  All: undefined,
  Twitter: 'twitter',
  YouTube: 'youtube',
  GitHub: 'github',
  HN: 'hackernews',
  Blogs: 'blog',
  arXiv: 'arxiv',
};

// ---------------------------------------------------------------------------
// IntelligenceCard
// ---------------------------------------------------------------------------

function IntelligenceCard({
  item,
  onClick,
}: {
  item: IntelligenceItem;
  onClick: () => void;
}) {
  const platformColor = platformTagColor[item.platform] || 'gray';

  return (
    <button
      onClick={onClick}
      className="
        w-full text-left px-4 py-3.5
        bg-bg-secondary border border-border rounded-lg
        hover:border-border-secondary
        transition-colors cursor-pointer group
      "
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      {/* Top row: platform + author + time */}
      <div className="flex items-center gap-2 mb-1.5">
        {item.status === 'unread' && <StatusDot color="blue" size="sm" />}
        <Tag label={item.platform} color={platformColor} size="sm" />
        {item.author && (
          <span className="text-[11px] font-[510] text-text-tertiary truncate">
            {item.author}
          </span>
        )}
        <span className="text-[10px] text-text-quaternary font-mono ml-auto shrink-0">
          {timeAgo(item.published_at)}
        </span>
      </div>

      {/* Title */}
      <p className="text-[14px] font-[510] text-text-secondary group-hover:text-text transition-colors leading-snug mb-1" style={{ transitionDuration: 'var(--duration-instant)' }}>
        {item.title}
      </p>

      {/* Summary */}
      {item.summary && (
        <p className="text-[12px] text-text-quaternary line-clamp-2 leading-relaxed mb-2">
          {item.summary}
        </p>
      )}

      {/* Bottom row: tags + score */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {item.relevance_tags.slice(0, 4).map(tag => (
          <span
            key={tag}
            className="inline-flex items-center h-[18px] px-1.5 rounded-xs text-[10px] bg-bg-tertiary text-text-tertiary"
          >
            {tag}
          </span>
        ))}
        <span className="ml-auto text-[10px] font-mono text-text-quaternary shrink-0">
          {item.relevance_score.toFixed(1)}
        </span>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function FeedSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="bg-bg-secondary border border-border rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <Skeleton className="h-5 w-14 rounded-xs" />
            <Skeleton className="h-3 w-20" />
            <div className="flex-1" />
            <Skeleton className="h-3 w-12" />
          </div>
          <Skeleton className="h-4 w-3/4 mb-1.5" />
          <Skeleton className="h-3 w-full mb-1" />
          <Skeleton className="h-3 w-2/3 mb-2.5" />
          <div className="flex gap-1.5">
            <Skeleton className="h-[18px] w-12 rounded-xs" />
            <Skeleton className="h-[18px] w-16 rounded-xs" />
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Feed page
// ---------------------------------------------------------------------------

export default function IntelligenceFeed() {
  const navigate = useNavigate();
  const [activePlatform, setActivePlatform] = useState<string>('All');
  const [search, setSearch] = useState('');
  const [searchDebounced, setSearchDebounced] = useState('');

  // Debounce search
  const handleSearch = (value: string) => {
    setSearch(value);
    clearTimeout((window as any).__intelSearchTimer);
    (window as any).__intelSearchTimer = setTimeout(() => setSearchDebounced(value), 300);
  };

  const platformFilter = platformFilterMap[activePlatform];
  const { data, isLoading, isError } = useIntelligenceFeed(
    7,
    100,
    platformFilter,
    undefined,
    searchDebounced || undefined,
  );
  const { data: stats } = useIntelligenceStats();

  const items = data?.items ?? [];

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[720px] mx-auto px-6 pt-10 pb-16">
        {/* Stats bar */}
        {stats && (
          <div className="flex items-center gap-3 mb-6 text-[11px] text-text-quaternary font-mono">
            <span className="tabular-nums">{stats.total_items} items</span>
            <span className="w-px h-3 bg-border" />
            <span className="tabular-nums">{stats.unread_count} unread</span>
            <span className="w-px h-3 bg-border" />
            <span className="tabular-nums">{stats.active_sources} sources</span>
          </div>
        )}

        {/* Filter pills + search */}
        <div className="flex items-center gap-2 mb-6 flex-wrap">
          {PLATFORMS.map(platform => (
            <button
              key={platform}
              onClick={() => setActivePlatform(platform)}
              className={`
                h-7 px-3 rounded-full text-[11px] font-[510] cursor-pointer
                transition-all
                ${activePlatform === platform
                  ? 'bg-accent text-white'
                  : 'text-text-quaternary hover:text-text-tertiary hover:bg-hover border border-transparent'}
              `}
              style={{ transitionDuration: 'var(--duration-instant)' }}
            >
              {platform}
            </button>
          ))}

          <div className="flex-1" />

          <div className="relative max-w-[220px] w-full">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3 h-3 text-text-quaternary" />
            <input
              type="text"
              value={search}
              onChange={e => handleSearch(e.target.value)}
              placeholder="Search..."
              className="
                w-full h-7 pl-7 pr-3
                bg-transparent border border-border rounded-full
                text-[11px] text-text-secondary placeholder:text-text-quaternary
                focus:border-border-secondary focus:outline-none
                transition-colors
              "
              style={{ transitionDuration: 'var(--duration-fast)' }}
            />
          </div>
        </div>

        {/* Error */}
        {isError && <ErrorBanner message="Failed to load intelligence feed." />}

        {/* Loading */}
        {isLoading && <FeedSkeleton />}

        {/* Empty */}
        {!isLoading && !isError && items.length === 0 && (
          <EmptyState
            icon={<Rss />}
            title={search ? 'No items match your search' : 'No intelligence items yet'}
            description={search ? 'Try a different query or clear filters.' : 'Items will appear here once sources are configured and ingested.'}
          />
        )}

        {/* Feed cards */}
        {!isLoading && items.length > 0 && (
          <div className="space-y-2">
            {items.map(item => (
              <IntelligenceCard
                key={item.id}
                item={item}
                onClick={() => navigate(`/intelligence/${item.id}`)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
