import { useState, useEffect } from 'react';
import { Search, Rss } from 'lucide-react';
import { EmptyState } from '@/components/primitives/EmptyState';
import { Skeleton } from '@/components/primitives/Skeleton';
import ItemDetailDrawer, { FeedItem } from '@/components/knowledge/ItemDetailDrawer';

// ---------------------------------------------------------------------------
// Helpers (mirrors IntelligenceFeed.tsx styling)
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
  return `${days}d ago`;
}

function isRealSummary(s: string | null): s is string {
  if (!s) return false;
  const urlChars = (s.match(/https?:\/\/\S+/g) || []).join('').length;
  return urlChars < s.length * 0.5 && s.length > 20;
}

const platformColors: Record<string, string> = {
  twitter: 'text-tag-blue',
  youtube: 'text-tag-red',
  github: 'text-tag-gray',
  hackernews: 'text-tag-orange',
  blog: 'text-tag-purple',
  arxiv: 'text-tag-green',
};

const FILTERS = [
  { label: 'All', value: '' },
  { label: 'Twitter', value: 'twitter' },
  { label: 'YouTube', value: 'youtube' },
  { label: 'GitHub', value: 'github' },
  { label: 'HN', value: 'hackernews' },
  { label: 'Blogs', value: 'blog' },
  { label: 'arXiv', value: 'arxiv' },
];

// ---------------------------------------------------------------------------
// Feed row
// ---------------------------------------------------------------------------

function FeedRow({ item, onClick }: { item: FeedItem; onClick: () => void }) {
  const isUnread = item.status === 'unread';
  const isHighRelevance = item.relevance_score >= 0.4;
  const showSummary = isHighRelevance && isRealSummary(item.summary);
  const platformColor = platformColors[item.platform] || 'text-tag-gray';

  return (
    <button
      onClick={onClick}
      className={`
        w-full text-left cursor-pointer group
        border-b border-border last:border-b-0
        transition-colors duration-75
        hover:bg-bg-secondary/50
        ${showSummary ? 'py-3 px-3' : 'py-2 px-3'}
      `}
    >
      {/* Primary line: platform · title ... time */}
      <div className="flex items-baseline gap-0 min-w-0">
        <span className={`text-[11px] font-[510] shrink-0 mr-2 ${platformColor}`}>
          {item.platform === 'hackernews' ? 'HN' : item.platform}
        </span>

        <span
          className={`
            text-[13px] leading-snug truncate min-w-0
            ${isUnread ? 'text-text font-[530]' : 'text-text-tertiary font-normal'}
          `}
          style={{ flex: '1 1 0%' }}
        >
          {item.title}
        </span>

        <span className="text-[11px] text-text-tertiary font-mono tabular-nums shrink-0 ml-3">
          {timeAgo(item.published_at)}
        </span>
      </div>

      {showSummary && (
        <p className="text-[12px] text-text-quaternary leading-relaxed mt-0.5 line-clamp-1">
          {(item.relevance_tags || []).length > 0 && (
            <span className="text-text-quaternary">
              {(item.relevance_tags || []).slice(0, 2).join(' · ')}{' · '}
            </span>
          )}
          {item.summary}
        </p>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------

interface Stats {
  total_items: number;
  unread_count: number;
  active_sources: number;
}

export default function KnowledgeFeed() {
  const [items, setItems] = useState<FeedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [platform, setPlatform] = useState('');
  const [search, setSearch] = useState('');
  const [stats, setStats] = useState<Stats | null>(null);
  const [drawerItem, setDrawerItem] = useState<FeedItem | null>(null);

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams();
    params.set('days', '30');
    params.set('limit', '100');
    if (platform) params.set('platform', platform);
    if (search) params.set('search', search);

    fetch(`/api/intelligence/feed?${params}`)
      .then(r => r.json())
      .then(data => {
        setItems(data.items || []);
        setLoading(false);
      })
      .catch(e => {
        setError(e.message);
        setLoading(false);
      });
  }, [platform, search]);

  useEffect(() => {
    fetch('/api/intelligence/stats')
      .then(r => r.json())
      .then(setStats)
      .catch(() => {});
  }, []);

  function handleItemChange(updated: FeedItem) {
    // If item was saved or dismissed, remove from list; otherwise update in place.
    if (updated.status === 'saved' || updated.status === 'dismissed') {
      setItems(prev => prev.filter(i => i.id !== updated.id));
      if (drawerItem?.id === updated.id && updated.status === 'dismissed') {
        setDrawerItem(null);
      } else if (drawerItem?.id === updated.id) {
        setDrawerItem(updated);
      }
    } else {
      setItems(prev => prev.map(i => (i.id === updated.id ? updated : i)));
      if (drawerItem?.id === updated.id) setDrawerItem(updated);
    }
  }

  function handleRowClick(item: FeedItem) {
    setDrawerItem(item);
    // Optimistic: mark as read locally when opened
    if (item.status === 'unread') {
      const updated = { ...item, status: 'read' as const };
      setItems(prev => prev.map(i => (i.id === item.id ? updated : i)));
    }
  }

  const unreadCount = stats?.unread_count ?? 0;
  const totalCount = stats?.total_items ?? items.length;

  return (
    <div className="h-full overflow-hidden flex">
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-[720px] mx-auto px-6 pt-6 pb-16">

          {/* Header: stats sentence + search */}
          <div className="flex items-center gap-3 mb-5">
            {stats && (
              <p className="text-[12px] text-text-tertiary">
                <span className={unreadCount > 0 ? 'text-text font-[510]' : ''}>
                  {unreadCount} unread
                </span>
                {' '}of {totalCount} · {stats.active_sources} sources
              </p>
            )}
            <div className="flex-1" />
            <div className="relative w-[180px]">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3 h-3 text-text-quaternary pointer-events-none" />
              <input
                type="text"
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Search..."
                className="w-full h-7 pl-7 pr-3 bg-transparent border border-border rounded-full text-[11px] text-text-secondary placeholder:text-text-quaternary focus:border-border-secondary focus:outline-none transition-colors duration-75"
              />
            </div>
          </div>

          {/* Filter pills — glass pill style */}
          <div className="flex items-center gap-1.5 mb-4 flex-wrap">
            {FILTERS.map(f => (
              <button
                key={f.value}
                onClick={() => setPlatform(f.value)}
                className={`h-6 px-2.5 rounded-full border text-[10px] font-[510] cursor-pointer transition-colors duration-75
                  ${platform === f.value
                    ? 'bg-bg-tertiary border-border text-text'
                    : 'bg-bg-secondary/50 border-border text-text-tertiary hover:text-text hover:bg-bg-tertiary'}`}
              >
                {f.label}
              </button>
            ))}
          </div>

          {error && <p className="text-red text-[12px] mb-4">Couldn't load feed — {error}</p>}

          {/* Loading skeleton */}
          {loading && (
            <div className="border-t border-border">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="py-2.5 px-3 border-b border-border flex items-center gap-3">
                  <Skeleton className="h-3.5 flex-1" />
                  <Skeleton className="h-3 w-8" />
                  <Skeleton className="h-3 w-10" />
                </div>
              ))}
            </div>
          )}

          {/* Empty */}
          {!loading && !error && items.length === 0 && (
            <EmptyState
              icon={<Rss />}
              title="No items yet"
              description="Configure sources in the Sources tab to start receiving intelligence."
            />
          )}

          {/* Feed — divider rows */}
          {!loading && items.length > 0 && (
            <div className="border-t border-border">
              {items.map(item => (
                <FeedRow
                  key={item.id}
                  item={item}
                  onClick={() => handleRowClick(item)}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Drawer */}
      {drawerItem && (
        <ItemDetailDrawer
          item={drawerItem}
          onClose={() => setDrawerItem(null)}
          onItemChange={handleItemChange}
        />
      )}
    </div>
  );
}
