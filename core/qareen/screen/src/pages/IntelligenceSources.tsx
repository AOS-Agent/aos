import { useState } from 'react';
import { Plus, Trash2, Globe, Rss } from 'lucide-react';
import {
  useIntelligenceSources,
  useCreateSource,
  useDeleteSource,
} from '@/hooks/useIntelligence';
import type { IntelligenceSource } from '@/hooks/useIntelligence';
import { Tag, type TagColor } from '@/components/primitives/Tag';
import { StatusDot } from '@/components/primitives/StatusDot';
import { Button } from '@/components/primitives/Button';
import { Input } from '@/components/primitives/Input';
import { EmptyState } from '@/components/primitives/EmptyState';
import { ErrorBanner } from '@/components/primitives/ErrorBanner';
import { Skeleton } from '@/components/primitives/Skeleton';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function timeAgo(iso: string | undefined | null): string {
  if (!iso) return 'never';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return `${Math.floor(days / 30) || 1}mo ago`;
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

const PLATFORM_OPTIONS = [
  'twitter', 'youtube', 'github', 'hackernews', 'blog', 'arxiv', 'reddit', 'rss',
] as const;

const PRIORITY_OPTIONS = ['high', 'medium', 'low'] as const;

// ---------------------------------------------------------------------------
// Source card
// ---------------------------------------------------------------------------

function SourceCard({
  source,
  onDelete,
  isDeleting,
}: {
  source: IntelligenceSource;
  onDelete: () => void;
  isDeleting: boolean;
}) {
  const platformColor = platformTagColor[source.platform] || 'gray';

  return (
    <div className="bg-bg-secondary border border-border rounded-lg px-4 py-3.5 group">
      {/* Header: name + platform + active dot */}
      <div className="flex items-center gap-2 mb-2">
        <StatusDot
          color={source.is_active ? 'green' : 'gray'}
          size="sm"
        />
        <span className="text-[13px] font-[510] text-text-secondary truncate flex-1">
          {source.name}
        </span>
        <Tag label={source.platform} color={platformColor} size="sm" />
      </div>

      {/* Route / URL */}
      {(source.route || source.route_url) && (
        <div className="flex items-center gap-1.5 mb-2">
          <Globe className="w-3 h-3 text-text-quaternary shrink-0" />
          <span className="text-[11px] text-text-quaternary truncate font-mono">
            {source.route || source.route_url}
          </span>
        </div>
      )}

      {/* Keywords */}
      {source.keywords.length > 0 && (
        <div className="flex items-center gap-1 flex-wrap mb-2">
          {source.keywords.map(kw => (
            <span
              key={kw}
              className="inline-flex items-center h-[18px] px-1.5 rounded-xs text-[10px] bg-bg-tertiary text-text-tertiary"
            >
              {kw}
            </span>
          ))}
        </div>
      )}

      {/* Bottom row: meta + actions */}
      <div className="flex items-center gap-3 text-[10px] text-text-quaternary font-mono">
        <span>Priority: {source.priority}</span>
        <span className="w-px h-3 bg-border" />
        <span className="tabular-nums">{source.items_total} items</span>
        <span className="w-px h-3 bg-border" />
        <span>Checked {timeAgo(source.last_checked)}</span>

        <button
          onClick={onDelete}
          disabled={isDeleting}
          className="
            ml-auto h-6 w-6 rounded-xs
            inline-flex items-center justify-center
            text-transparent group-hover:text-text-quaternary
            hover:!text-red hover:bg-red-muted
            transition-colors cursor-pointer
            disabled:opacity-40 disabled:pointer-events-none
          "
          style={{ transitionDuration: 'var(--duration-instant)' }}
          title="Delete source"
        >
          <Trash2 className="w-3 h-3" />
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Add source form
// ---------------------------------------------------------------------------

function AddSourceForm({ onClose }: { onClose: () => void }) {
  const createSource = useCreateSource();
  const [name, setName] = useState('');
  const [platform, setPlatform] = useState<string>('rss');
  const [route, setRoute] = useState('');
  const [routeUrl, setRouteUrl] = useState('');
  const [priority, setPriority] = useState<string>('medium');
  const [keywords, setKeywords] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;

    const kws = keywords
      .split(',')
      .map(k => k.trim())
      .filter(Boolean);

    createSource.mutate(
      {
        name: name.trim(),
        platform,
        route: route.trim() || undefined,
        route_url: routeUrl.trim() || undefined,
        priority,
        keywords: kws.length > 0 ? kws : undefined,
      },
      {
        onSuccess: () => {
          onClose();
        },
      },
    );
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="bg-bg-secondary border border-border rounded-lg p-4 mb-4"
    >
      <div className="grid grid-cols-2 gap-3 mb-3">
        <Input
          label="Name"
          value={name}
          onChange={e => setName(e.target.value)}
          placeholder="e.g. Hacker News Front Page"
          required
        />
        <div className="flex flex-col">
          <label className="text-xs font-medium text-text-tertiary mb-1.5">
            Platform
          </label>
          <select
            value={platform}
            onChange={e => setPlatform(e.target.value)}
            className="
              h-9 w-full px-2.5
              rounded-sm border border-border-secondary bg-bg-tertiary
              text-sm text-text
              transition-colors duration-100
              focus:outline-none focus:border-accent focus:ring-2 focus:ring-accent/20
            "
          >
            {PLATFORM_OPTIONS.map(p => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 mb-3">
        <Input
          label="Route (RSSHub path)"
          value={route}
          onChange={e => setRoute(e.target.value)}
          placeholder="/hn/frontpage"
        />
        <Input
          label="Route URL (direct feed URL)"
          value={routeUrl}
          onChange={e => setRouteUrl(e.target.value)}
          placeholder="https://example.com/feed.xml"
        />
      </div>

      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="flex flex-col">
          <label className="text-xs font-medium text-text-tertiary mb-1.5">
            Priority
          </label>
          <select
            value={priority}
            onChange={e => setPriority(e.target.value)}
            className="
              h-9 w-full px-2.5
              rounded-sm border border-border-secondary bg-bg-tertiary
              text-sm text-text
              transition-colors duration-100
              focus:outline-none focus:border-accent focus:ring-2 focus:ring-accent/20
            "
          >
            {PRIORITY_OPTIONS.map(p => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
        <Input
          label="Keywords (comma-separated)"
          value={keywords}
          onChange={e => setKeywords(e.target.value)}
          placeholder="AI, LLM, systems"
        />
      </div>

      <div className="flex items-center gap-2">
        <Button
          type="submit"
          variant="primary"
          size="sm"
          disabled={!name.trim() || createSource.isPending}
        >
          {createSource.isPending ? 'Adding...' : 'Add source'}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function SourcesSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="bg-bg-secondary border border-border rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <Skeleton className="w-1.5 h-1.5 rounded-full" />
            <Skeleton className="h-4 w-32" />
            <div className="flex-1" />
            <Skeleton className="h-5 w-14 rounded-xs" />
          </div>
          <Skeleton className="h-3 w-48 mb-2" />
          <div className="flex gap-1.5 mb-2">
            <Skeleton className="h-[18px] w-10 rounded-xs" />
            <Skeleton className="h-[18px] w-12 rounded-xs" />
          </div>
          <Skeleton className="h-3 w-64" />
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sources page
// ---------------------------------------------------------------------------

export default function IntelligenceSources() {
  const [showForm, setShowForm] = useState(false);
  const { data, isLoading, isError } = useIntelligenceSources();
  const deleteSource = useDeleteSource();

  const sources = data?.sources ?? [];

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[720px] mx-auto px-6 pt-10 pb-16">
        {/* Add button */}
        <div className="flex items-center gap-2 mb-6">
          <span className="text-[11px] text-text-quaternary font-mono tabular-nums">
            {sources.length} source{sources.length !== 1 ? 's' : ''}
          </span>
          <div className="flex-1" />
          {!showForm && (
            <Button
              variant="primary"
              size="sm"
              icon={<Plus />}
              onClick={() => setShowForm(true)}
            >
              Add source
            </Button>
          )}
        </div>

        {/* Add form */}
        {showForm && <AddSourceForm onClose={() => setShowForm(false)} />}

        {/* Error */}
        {isError && <ErrorBanner message="Failed to load intelligence sources." />}

        {/* Loading */}
        {isLoading && <SourcesSkeleton />}

        {/* Empty */}
        {!isLoading && !isError && sources.length === 0 && (
          <EmptyState
            icon={<Rss />}
            title="No sources configured"
            description="Add your first intelligence source to start monitoring."
            action={
              !showForm ? (
                <Button
                  variant="primary"
                  size="sm"
                  icon={<Plus />}
                  onClick={() => setShowForm(true)}
                >
                  Add source
                </Button>
              ) : undefined
            }
          />
        )}

        {/* Source list */}
        {!isLoading && sources.length > 0 && (
          <div className="space-y-2">
            {sources.map(source => (
              <SourceCard
                key={source.id}
                source={source}
                onDelete={() => deleteSource.mutate(source.id)}
                isDeleting={deleteSource.isPending}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
