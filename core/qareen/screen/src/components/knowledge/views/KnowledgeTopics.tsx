import { useState, useEffect, useMemo } from 'react';
import { ChevronLeft, FolderTree, Search } from 'lucide-react';
import { Skeleton } from '@/components/primitives/Skeleton';

interface Topic {
  slug: string;
  title: string;
  orientation: string;
  doc_count: number;
  captures_count: number;
  research_count: number;
  synthesis_count: number;
  decisions_count: number;
  expertise_count: number;
  open_questions: string[];
  updated: string;
  file_mtime: string;
  synthesis_suggested: boolean;
}

interface TopicsResponse {
  topics: Topic[];
  total: number;
}

interface Entry {
  path: string;
  title: string;
  type: string;
  stage: number;
  date: string;
  summary: string;
}

interface TopicDetail {
  slug: string;
  title: string;
  orientation: string;
  updated: string;
  doc_count: number;
  captures: Entry[];
  research: Entry[];
  synthesis: Entry[];
  decisions: Entry[];
  expertise: Entry[];
  open_questions: string[];
}

type SortMode = 'updated' | 'docs' | 'alpha';
type ViewMode = 'grid' | 'detail';

function timeAgo(iso: string | null): string {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (isNaN(then)) return 'never';
  const diff = Date.now() - then;
  if (diff < 0) return 'just now';
  const s = Math.floor(diff / 1000);
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  const mo = Math.floor(d / 30);
  if (mo < 12) return `${mo}mo ago`;
  return `${Math.floor(mo / 12)}y ago`;
}

function formatDate(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toISOString().slice(0, 10);
}

const SORT_OPTIONS: { key: SortMode; label: string }[] = [
  { key: 'updated', label: 'Updated' },
  { key: 'docs', label: 'Most Docs' },
  { key: 'alpha', label: 'A–Z' },
];

const SECTION_DEFS: { key: keyof Pick<TopicDetail, 'captures' | 'research' | 'synthesis' | 'decisions' | 'expertise'>; label: string }[] = [
  { key: 'captures', label: 'Captures' },
  { key: 'research', label: 'Research' },
  { key: 'synthesis', label: 'Synthesis' },
  { key: 'decisions', label: 'Decisions' },
  { key: 'expertise', label: 'Expertise' },
];

export default function KnowledgeTopics() {
  const [mode, setMode] = useState<ViewMode>('grid');
  const [data, setData] = useState<TopicsResponse | null>(null);
  const [detail, setDetail] = useState<TopicDetail | null>(null);
  const [activeSlug, setActiveSlug] = useState<string>('');
  const [sort, setSort] = useState<SortMode>('updated');
  const [search, setSearch] = useState<string>('');
  const [listLoading, setListLoading] = useState<boolean>(true);
  const [detailLoading, setDetailLoading] = useState<boolean>(false);
  const [listError, setListError] = useState<Error | null>(null);
  const [detailError, setDetailError] = useState<Error | null>(null);

  // Fetch list on sort change
  useEffect(() => {
    let cancelled = false;
    setListLoading(true);
    fetch(`/api/knowledge/topics?sort=${sort}&limit=100`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((json: TopicsResponse) => {
        if (!cancelled) {
          setData(json);
          setListError(null);
        }
      })
      .catch((e: Error) => {
        if (!cancelled) setListError(e);
      })
      .finally(() => {
        if (!cancelled) setListLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sort]);

  // Fetch detail on activeSlug change
  useEffect(() => {
    if (!activeSlug) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetail(null);
    fetch(`/api/knowledge/topics/${encodeURIComponent(activeSlug)}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((json: TopicDetail) => {
        if (!cancelled) {
          setDetail(json);
          setDetailError(null);
        }
      })
      .catch((e: Error) => {
        if (!cancelled) setDetailError(e);
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeSlug]);

  const filteredTopics = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    if (!q) return data.topics;
    return data.topics.filter((t) => {
      return (
        t.slug.toLowerCase().includes(q) ||
        t.title.toLowerCase().includes(q) ||
        (t.orientation ?? '').toLowerCase().includes(q)
      );
    });
  }, [data, search]);

  const openDetail = (slug: string) => {
    setActiveSlug(slug);
    setMode('detail');
  };

  const backToGrid = () => {
    setMode('grid');
    setActiveSlug('');
    setDetail(null);
    setDetailError(null);
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[1080px] mx-auto px-6 pt-6 pb-16">
        {mode === 'grid' ? (
          <GridView
            data={data}
            filteredTopics={filteredTopics}
            loading={listLoading}
            error={listError}
            sort={sort}
            setSort={setSort}
            search={search}
            setSearch={setSearch}
            onOpen={openDetail}
          />
        ) : (
          <DetailView
            detail={detail}
            loading={detailLoading}
            error={detailError}
            onBack={backToGrid}
          />
        )}
      </div>
    </div>
  );
}

/* -------------------- Grid View -------------------- */

interface GridViewProps {
  data: TopicsResponse | null;
  filteredTopics: Topic[];
  loading: boolean;
  error: Error | null;
  sort: SortMode;
  setSort: (s: SortMode) => void;
  search: string;
  setSearch: (s: string) => void;
  onOpen: (slug: string) => void;
}

function GridView({
  data,
  filteredTopics,
  loading,
  error,
  sort,
  setSort,
  search,
  setSearch,
  onOpen,
}: GridViewProps) {
  return (
    <>
      {/* Header row */}
      <div className="flex items-center justify-between gap-4 mb-6 flex-wrap">
        <div className="flex items-center gap-2">
          {SORT_OPTIONS.map((opt) => {
            const active = sort === opt.key;
            return (
              <button
                key={opt.key}
                onClick={() => setSort(opt.key)}
                className={
                  'h-6 px-3 rounded-full border text-[10px] uppercase tracking-wide transition-colors ' +
                  (active
                    ? 'bg-accent/15 border-accent/40 text-accent'
                    : 'bg-bg-secondary/50 border-border text-text-tertiary hover:text-text')
                }
              >
                {opt.label}
              </button>
            );
          })}
        </div>

        <div className="flex items-center gap-3">
          <div className="relative">
            <Search
              size={12}
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-quaternary"
            />
            <input
              type="text"
              placeholder="Search topics…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="h-7 pl-7 pr-3 rounded-full bg-bg-secondary/50 border border-border text-[11px] text-text placeholder:text-text-quaternary focus:outline-none focus:border-accent/40 w-[180px]"
            />
          </div>
          <span className="font-mono text-[10px] text-text-quaternary">
            {data ? `${data.total} topic${data.total === 1 ? '' : 's'}` : ''}
          </span>
        </div>
      </div>

      {error && (
        <p className="text-red text-[12px]">Couldn't load topics — {error.message}</p>
      )}

      {loading && !data && !error && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-[180px] rounded-2xl" />
          ))}
        </div>
      )}

      {data && !error && data.topics.length === 0 && (
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <FolderTree size={24} className="text-text-quaternary mb-3" />
          <p className="text-[12px] text-text-tertiary">
            No topic indexes yet. Topics auto-create as captures are compiled.
          </p>
        </div>
      )}

      {data && !error && data.topics.length > 0 && filteredTopics.length === 0 && (
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <p className="text-[12px] text-text-tertiary">No topics match</p>
        </div>
      )}

      {data && !error && filteredTopics.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {filteredTopics.map((t) => (
            <TopicCard key={t.slug} topic={t} onClick={() => onOpen(t.slug)} />
          ))}
        </div>
      )}
    </>
  );
}

function TopicCard({ topic, onClick }: { topic: Topic; onClick: () => void }) {
  const chips: { label: string; count: number }[] = [
    { label: 'cap', count: topic.captures_count },
    { label: 'res', count: topic.research_count },
    { label: 'syn', count: topic.synthesis_count },
    { label: 'dec', count: topic.decisions_count },
    { label: 'exp', count: topic.expertise_count },
  ];
  return (
    <button
      onClick={onClick}
      className="text-left bg-bg-secondary/40 border border-border rounded-2xl p-4 hover:border-accent/40 transition-colors flex flex-col gap-3"
    >
      <div>
        <h3 className="text-[14px] text-text leading-tight">{topic.title}</h3>
        <p className="text-[10px] font-mono text-text-quaternary mt-0.5 truncate">
          {topic.slug}
        </p>
      </div>

      <p
        className="text-[12px] text-text-tertiary line-clamp-3"
        style={{ minHeight: 48 }}
      >
        {topic.orientation || 'No orientation yet.'}
      </p>

      <div className="flex items-center gap-2 flex-wrap text-[10px] font-mono text-text-tertiary">
        {chips.map((c) => (
          <span key={c.label}>
            {c.label} {c.count}
          </span>
        ))}
      </div>

      <div className="flex items-center justify-between gap-2 mt-auto pt-1">
        <span className="font-mono text-[10px] text-text-quaternary">
          {timeAgo(topic.updated)}
        </span>
        {topic.synthesis_suggested && (
          <span className="text-[10px] uppercase tracking-wide text-accent bg-accent/10 border border-accent/30 rounded-full px-2 py-0.5">
            Synthesis ready
          </span>
        )}
      </div>
    </button>
  );
}

/* -------------------- Detail View -------------------- */

interface DetailViewProps {
  detail: TopicDetail | null;
  loading: boolean;
  error: Error | null;
  onBack: () => void;
}

function DetailView({ detail, loading, error, onBack }: DetailViewProps) {
  return (
    <>
      <div className="flex items-start justify-between gap-4 mb-6">
        <button
          onClick={onBack}
          className="flex items-center gap-1 text-[11px] text-text-tertiary hover:text-text"
        >
          <ChevronLeft size={14} />
          All topics
        </button>
        {detail && (
          <span className="font-mono text-[10px] text-text-quaternary shrink-0">
            updated {timeAgo(detail.updated)}
          </span>
        )}
      </div>

      {error && (
        <p className="text-red text-[12px]">Couldn't load topic — {error.message}</p>
      )}

      {loading && !detail && !error && (
        <div className="space-y-4">
          <Skeleton className="h-[28px] w-[240px] rounded-md" />
          <Skeleton className="h-[80px] max-w-[680px] rounded-xl" />
          <Skeleton className="h-[40px] rounded-md" />
          <Skeleton className="h-[40px] rounded-md" />
          <Skeleton className="h-[40px] rounded-md" />
        </div>
      )}

      {detail && !error && (
        <>
          <div className="mb-6">
            <h1 className="text-[24px] font-[650] text-text tracking-[-0.01em]">
              {detail.title}
            </h1>
            <p className="text-[10px] font-mono text-text-quaternary mt-1">
              {detail.slug} · {detail.doc_count} doc{detail.doc_count === 1 ? '' : 's'}
            </p>
          </div>

          {detail.orientation && (
            <p className="font-serif text-[16px] text-text-secondary leading-relaxed max-w-[680px] mb-10">
              {detail.orientation}
            </p>
          )}

          {detail.open_questions && detail.open_questions.length > 0 && (
            <section className="mb-10 max-w-[680px]">
              <h2 className="text-[11px] uppercase tracking-wide text-text-quaternary mb-3">
                Open Questions
              </h2>
              <div className="bg-bg-secondary/30 border border-border rounded-xl p-4">
                <ul className="list-disc pl-4 space-y-1.5">
                  {detail.open_questions.map((q, i) => (
                    <li key={i} className="text-[12px] text-text-secondary leading-relaxed">
                      {q}
                    </li>
                  ))}
                </ul>
              </div>
            </section>
          )}

          {SECTION_DEFS.map(({ key, label }) => {
            const entries = detail[key] as Entry[];
            if (!entries || entries.length === 0) return null;
            return (
              <section key={key} className="mb-10">
                <div className="flex items-baseline gap-2 mb-3">
                  <h2 className="text-[11px] uppercase tracking-wide text-text-quaternary">
                    {label}
                  </h2>
                  <span className="font-mono text-[10px] text-text-quaternary">
                    {entries.length}
                  </span>
                </div>
                <ul>
                  {entries.map((entry) => (
                    <li
                      key={entry.path}
                      className="border-b border-border py-2.5"
                    >
                      <button
                        onClick={() => console.log('open entry', entry.path)}
                        className="w-full text-left group"
                      >
                        <div className="flex items-baseline justify-between gap-3">
                          <span className="text-[13px] text-text group-hover:text-accent transition-colors truncate">
                            {entry.title}
                          </span>
                          <div className="flex items-center gap-2 shrink-0">
                            <span className="text-[10px] uppercase tracking-wide text-text-quaternary bg-bg-secondary/50 border border-border rounded-full px-2 py-0.5">
                              stage {entry.stage}
                            </span>
                            <span className="font-mono text-[11px] text-text-quaternary">
                              {formatDate(entry.date)}
                            </span>
                          </div>
                        </div>
                        {entry.summary && (
                          <p className="text-[12px] text-text-tertiary line-clamp-2 mt-1">
                            {entry.summary}
                          </p>
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </>
      )}
    </>
  );
}
