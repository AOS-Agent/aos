import { useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, ExternalLink, Bookmark, X, Check } from 'lucide-react';
import { useIntelligenceItem, useMarkRead, useSaveItem, useDismissItem } from '@/hooks/useIntelligence';
import { Tag, type TagColor } from '@/components/primitives/Tag';
import { Button } from '@/components/primitives/Button';
import { MarkdownRenderer } from '@/components/primitives/MarkdownRenderer';
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

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function DetailSkeleton() {
  return (
    <div>
      <Skeleton className="h-5 w-14 rounded-xs mb-4" />
      <div className="flex items-center gap-2 mb-3">
        <Skeleton className="h-5 w-16 rounded-xs" />
        <Skeleton className="h-3 w-24" />
      </div>
      <Skeleton className="h-7 w-3/4 mb-2" />
      <Skeleton className="h-7 w-1/2 mb-8" />
      <Skeleton className="h-4 w-full mb-2" />
      <Skeleton className="h-4 w-full mb-2" />
      <Skeleton className="h-4 w-5/6 mb-2" />
      <Skeleton className="h-4 w-full mb-2" />
      <Skeleton className="h-4 w-2/3 mb-2" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail page
// ---------------------------------------------------------------------------

export default function IntelligenceDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: item, isLoading, isError } = useIntelligenceItem(id ?? null);
  const markRead = useMarkRead();
  const saveItem = useSaveItem();
  const dismissItem = useDismissItem();

  // Mark as read on mount
  useEffect(() => {
    if (id && item && item.status === 'unread') {
      markRead.mutate(id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, item?.status]);

  const isSaved = item?.status === 'saved';
  const isDismissed = item?.status === 'dismissed';
  const platformColor = platformTagColor[item?.platform ?? ''] || 'gray';

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[720px] mx-auto px-6 pt-10 pb-16">
        {/* Back */}
        <button
          onClick={() => navigate('/intelligence')}
          className="flex items-center gap-1.5 text-[11px] text-text-quaternary hover:text-text-tertiary transition-colors mb-6 cursor-pointer"
          style={{ transitionDuration: 'var(--duration-instant)' }}
        >
          <ArrowLeft className="w-3 h-3" />
          Back to feed
        </button>

        {/* Error */}
        {isError && <ErrorBanner message="Failed to load this item." />}

        {/* Loading */}
        {isLoading && <DetailSkeleton />}

        {/* Content */}
        {item && (
          <>
            {/* Platform + author + time */}
            <div className="flex items-center gap-2 mb-4">
              <Tag label={item.platform} color={platformColor} size="sm" />
              {item.author && (
                <span className="text-[12px] font-[510] text-text-tertiary">
                  {item.author}
                </span>
              )}
              <span className="text-[11px] text-text-quaternary font-mono">
                {timeAgo(item.published_at)}
              </span>
            </div>

            {/* Title */}
            <h2 className="text-[22px] font-[600] font-serif text-text leading-tight mb-6">
              {item.title}
            </h2>

            {/* Relevance tags */}
            {item.relevance_tags.length > 0 && (
              <div className="flex items-center gap-1.5 flex-wrap mb-6">
                {item.relevance_tags.map(tag => (
                  <span
                    key={tag}
                    className="inline-flex items-center h-[18px] px-1.5 rounded-xs text-[10px] bg-bg-tertiary text-text-tertiary"
                  >
                    {tag}
                  </span>
                ))}
                <span className="text-[10px] font-mono text-text-quaternary ml-1">
                  Score: {item.relevance_score.toFixed(2)}
                </span>
              </div>
            )}

            {/* Content body */}
            {item.content ? (
              <div className="mb-8">
                <MarkdownRenderer content={item.content} />
              </div>
            ) : item.summary ? (
              <p className="text-[15px] leading-[1.75] text-text-secondary font-serif mb-8">
                {item.summary}
              </p>
            ) : (
              <p className="text-[13px] text-text-quaternary italic mb-8">
                No content available for this item.
              </p>
            )}

            {/* Source link */}
            {item.url && (
              <a
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-[12px] text-text-tertiary hover:text-accent transition-colors mb-8"
                style={{ transitionDuration: 'var(--duration-instant)' }}
              >
                <ExternalLink className="w-3 h-3" />
                {item.source_name || 'View original'}
              </a>
            )}

            {/* Vault path if saved */}
            {item.vault_path && (
              <div className="flex items-center gap-1.5 text-[11px] text-text-quaternary font-mono mb-6">
                <Check className="w-3 h-3 text-green" />
                Saved to {item.vault_path}
              </div>
            )}

            {/* Actions */}
            <div className="flex items-center gap-2 pt-4 border-t border-border">
              {isSaved ? (
                <Button variant="secondary" size="sm" icon={<Check />} disabled>
                  Saved to vault
                </Button>
              ) : (
                <Button
                  variant="primary"
                  size="sm"
                  icon={<Bookmark />}
                  onClick={() => id && saveItem.mutate(id)}
                  disabled={saveItem.isPending || isDismissed}
                >
                  {saveItem.isPending ? 'Saving...' : 'Save to vault'}
                </Button>
              )}

              {isDismissed ? (
                <Button variant="secondary" size="sm" icon={<X />} disabled>
                  Dismissed
                </Button>
              ) : (
                <Button
                  variant="secondary"
                  size="sm"
                  icon={<X />}
                  onClick={() => id && dismissItem.mutate(id)}
                  disabled={dismissItem.isPending || isSaved}
                >
                  {dismissItem.isPending ? 'Dismissing...' : 'Dismiss'}
                </Button>
              )}

              {item.url && (
                <a
                  href={item.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="ml-auto"
                >
                  <Button variant="ghost" size="sm" icon={<ExternalLink />}>
                    Open original
                  </Button>
                </a>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
