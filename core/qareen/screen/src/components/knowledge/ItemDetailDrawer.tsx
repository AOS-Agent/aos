import { useEffect, useState } from 'react';
import { X, ExternalLink, Loader2 } from 'lucide-react';

// ---------------------------------------------------------------------------
// Types + helpers (kept local to avoid cross-file refactors)
// ---------------------------------------------------------------------------

export interface FeedItem {
  id: string;
  title: string;
  summary: string | null;
  content: string | null;
  content_status: 'pending' | 'extracting' | 'extracted' | 'failed';
  url: string;
  author: string | null;
  platform: string;
  source_name: string | null;
  published_at: string | null;
  relevance_score: number;
  relevance_tags: string[] | null;
  status: 'unread' | 'read' | 'saved' | 'dismissed';
}

interface SaveResponse {
  status: 'saved' | 'proposal_pending' | 'already_saved';
  proposal_id?: string;
  auto_accepted?: boolean;
  vault_path?: string;
  filename?: string;
  topic?: { slug: string; confidence: number; is_new: boolean; index_path: string | null };
  concepts?: string[];
  entities?: Array<{ type: string; name: string; confidence: number }>;
  summary?: string;
  links_created?: number;
  reason?: string;
  compilation?: { model: string; provider: string; duration_ms: number; template: string };
}

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

const platformColors: Record<string, string> = {
  twitter: 'text-tag-blue',
  youtube: 'text-tag-red',
  github: 'text-tag-gray',
  hackernews: 'text-tag-orange',
  blog: 'text-tag-purple',
  arxiv: 'text-tag-green',
};

// ---------------------------------------------------------------------------
// Drawer
// ---------------------------------------------------------------------------

interface Props {
  item: FeedItem;
  onClose: () => void;
  onItemChange?: (updated: FeedItem) => void;
}

export default function ItemDetailDrawer({ item, onClose, onItemChange }: Props) {
  const [current, setCurrent] = useState<FeedItem>(item);
  const [mounted, setMounted] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveResult, setSaveResult] = useState<SaveResponse | null>(null);
  const [actionError, setActionError] = useState<string>('');

  // Slide-in animation trigger
  useEffect(() => {
    const id = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(id);
  }, []);

  // Sync when parent swaps in a new item
  useEffect(() => {
    setCurrent(item);
    setSaveResult(null);
    setActionError('');
  }, [item.id]);

  // ESC to close
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Trigger extract if content is missing / pending
  useEffect(() => {
    if (current.content_status === 'pending' || (current.content === null && current.content_status !== 'extracting')) {
      setCurrent(c => ({ ...c, content_status: 'extracting' }));
      fetch(`/api/intelligence/items/${current.id}/extract`, { method: 'POST' })
        .then(r => r.json())
        .then((data: { status: string; cached: boolean; item: FeedItem }) => {
          if (data.item) {
            setCurrent(data.item);
            onItemChange?.(data.item);
          }
        })
        .catch(() => {
          setCurrent(c => ({ ...c, content_status: 'failed' }));
        });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current.id]);

  function handleClose() {
    setMounted(false);
    setTimeout(onClose, 200);
  }

  async function handleSave() {
    setSaving(true);
    setActionError('');
    try {
      const r = await fetch(`/api/intelligence/items/${current.id}/save`, { method: 'POST' });
      const data: SaveResponse = await r.json();
      setSaveResult(data);
      if (data.status === 'saved' || data.status === 'already_saved') {
        const updated: FeedItem = { ...current, status: 'saved' };
        setCurrent(updated);
        onItemChange?.(updated);
      }
    } catch (e: any) {
      setActionError(e?.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  async function handleDismiss() {
    try {
      await fetch(`/api/intelligence/items/${current.id}/dismiss`, { method: 'POST' });
      const updated: FeedItem = { ...current, status: 'dismissed' };
      onItemChange?.(updated);
      handleClose();
    } catch (e: any) {
      setActionError(e?.message || 'Dismiss failed');
    }
  }

  async function handleMarkRead() {
    try {
      await fetch(`/api/intelligence/items/${current.id}/read`, { method: 'POST' });
      const updated: FeedItem = { ...current, status: 'read' };
      setCurrent(updated);
      onItemChange?.(updated);
    } catch (e: any) {
      setActionError(e?.message || 'Mark read failed');
    }
  }

  const platformColor = platformColors[current.platform] || 'text-tag-gray';
  const platformLabel = current.platform === 'hackernews' ? 'HN' : current.platform;

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 bg-black/30 z-40 transition-opacity duration-200 ${mounted ? 'opacity-100' : 'opacity-0'}`}
        onClick={handleClose}
      />

      {/* Drawer panel */}
      <div
        className={`
          fixed top-0 right-0 bottom-0 z-50
          w-full lg:w-[60vw]
          bg-bg-panel border-l border-border
          rounded-l-lg
          flex flex-col
          transition-transform duration-200 ease-out
          ${mounted ? 'translate-x-0' : 'translate-x-full'}
        `}
      >
        {/* Header */}
        <div className="flex items-start gap-3 px-5 py-4 border-b border-border shrink-0">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className={`text-[11px] font-[510] ${platformColor}`}>{platformLabel}</span>
              {current.source_name && (
                <span className="text-[11px] text-text-tertiary truncate">· {current.source_name}</span>
              )}
              <span className="text-[11px] text-text-quaternary font-mono tabular-nums">
                · {timeAgo(current.published_at)}
              </span>
            </div>
            <h2 className="text-[15px] font-[530] text-text leading-snug truncate">
              {current.title}
            </h2>
          </div>
          <button
            onClick={handleClose}
            className="shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-text-tertiary hover:text-text hover:bg-bg-tertiary transition-colors duration-75"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-5">
          {/* Save result view */}
          {saveResult && (saveResult.status === 'saved' || saveResult.status === 'already_saved') ? (
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-mono text-green">
                  {saveResult.status === 'already_saved' ? 'ALREADY SAVED' : 'SAVED'}
                </span>
                {saveResult.topic && (
                  <span className="text-[12px] text-text-tertiary">
                    Compiled into <span className="text-text font-[510]">{saveResult.topic.slug}</span>
                    {saveResult.topic.is_new && <span className="text-text-quaternary"> · new topic</span>}
                  </span>
                )}
              </div>
              {saveResult.summary && (
                <p className="font-serif text-[14px] leading-relaxed text-text-secondary">
                  {saveResult.summary}
                </p>
              )}
              {saveResult.concepts && saveResult.concepts.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {saveResult.concepts.map(c => (
                    <span
                      key={c}
                      className="text-[11px] px-2 py-0.5 rounded-md bg-bg-secondary border border-border text-text-tertiary"
                    >
                      {c}
                    </span>
                  ))}
                </div>
              )}
              {saveResult.vault_path && (
                <div className="pt-2 border-t border-border">
                  <p className="text-[11px] text-text-quaternary mb-1">Vault path</p>
                  <p className="text-[12px] font-mono text-text-tertiary break-all">{saveResult.vault_path}</p>
                </div>
              )}
              {saveResult.compilation && (
                <p className="text-[10px] text-text-quaternary font-mono">
                  {saveResult.compilation.provider}/{saveResult.compilation.model} · {saveResult.compilation.duration_ms}ms
                </p>
              )}
            </div>
          ) : saveResult && saveResult.status === 'proposal_pending' ? (
            <div className="space-y-3">
              <span className="text-[11px] font-mono text-accent">PENDING REVIEW</span>
              <p className="font-serif text-[14px] leading-relaxed text-text-secondary">
                {saveResult.reason || 'This item is queued for your review before it is saved.'}
              </p>
            </div>
          ) : (
            <>
              {/* Author */}
              {current.author && (
                <p className="text-[12px] text-text-tertiary mb-3">by {current.author}</p>
              )}

              {/* Relevance */}
              {(current.relevance_score > 0 || (current.relevance_tags && current.relevance_tags.length > 0)) && (
                <div className="flex items-center gap-2 flex-wrap mb-5">
                  {current.relevance_score > 0 && (
                    <span className="text-[10px] font-mono text-text-quaternary">
                      relevance {(current.relevance_score * 100).toFixed(0)}%
                    </span>
                  )}
                  {(current.relevance_tags || []).map(t => (
                    <span
                      key={t}
                      className="text-[10px] px-1.5 py-0.5 rounded-md bg-bg-secondary border border-border text-text-tertiary"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              )}

              {/* Content */}
              {current.content_status === 'extracting' && (
                <div className="flex items-center gap-2 text-[12px] text-text-tertiary py-6">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  Extracting full content...
                </div>
              )}

              {current.content_status === 'failed' && (
                <p className="text-[12px] text-red py-2">Extraction failed</p>
              )}

              {current.content && current.content_status !== 'extracting' && (
                <div
                  className="font-serif text-[14px] leading-relaxed text-text-secondary"
                  style={{ whiteSpace: 'pre-wrap' }}
                >
                  {current.content}
                </div>
              )}

              {!current.content && current.content_status === 'extracted' && current.summary && (
                <div
                  className="font-serif text-[14px] leading-relaxed text-text-secondary"
                  style={{ whiteSpace: 'pre-wrap' }}
                >
                  {current.summary}
                </div>
              )}
            </>
          )}

          {actionError && (
            <p className="text-red text-[12px] mt-4">{actionError}</p>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center gap-2 px-5 py-3 border-t border-border bg-bg-panel shrink-0">
          <button
            onClick={handleSave}
            disabled={saving || current.status === 'saved'}
            className="h-8 px-4 rounded-full bg-accent text-white text-[12px] font-[510] hover:bg-accent-subtle disabled:opacity-50 disabled:cursor-not-allowed transition-colors duration-75 flex items-center gap-1.5"
          >
            {saving && <Loader2 className="w-3 h-3 animate-spin" />}
            {current.status === 'saved' ? 'Saved' : 'Save to vault'}
          </button>
          <button
            onClick={handleMarkRead}
            disabled={current.status === 'read' || current.status === 'saved'}
            className="h-8 px-3 rounded-full text-[12px] text-text-tertiary hover:text-text hover:bg-bg-tertiary disabled:opacity-50 disabled:cursor-not-allowed transition-colors duration-75"
          >
            Mark read
          </button>
          <button
            onClick={handleDismiss}
            className="h-8 px-3 rounded-full text-[12px] text-text-tertiary hover:text-text hover:bg-bg-tertiary transition-colors duration-75"
          >
            Dismiss
          </button>
          <div className="flex-1" />
          <a
            href={current.url}
            target="_blank"
            rel="noopener noreferrer"
            className="h-8 px-3 rounded-full text-[12px] text-text-tertiary hover:text-text hover:bg-bg-tertiary transition-colors duration-75 flex items-center gap-1.5"
          >
            <ExternalLink className="w-3 h-3" />
            Open source
          </a>
        </div>
      </div>
    </>
  );
}
