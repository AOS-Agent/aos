import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Radio, Sparkles, AlertCircle } from 'lucide-react';
import { Skeleton } from '@/components/primitives/Skeleton';
import { BootstrapCard } from '@/components/knowledge/BootstrapCard';

interface IncomingItem {
  id: string;
  title: string;
  url: string;
  platform: string;
  source_name: string | null;
  relevance_score: number;
  published_at: string | null;
}

interface CompiledItem {
  id: string;
  vault_path: string;
  topic: string;
  summary: string;
  concepts: string[];
  status: string;
  created_at: string;
}

type AttentionItem =
  | { kind: 'pending_proposal'; id: string; title: string; topic: string; confidence: number; created_at: string }
  | { kind: 'orphan'; path: string; title: string; stage: number; type: string };

interface TopicActivity {
  slug: string;
  title: string;
  doc_count: number;
  updated: string;
  orientation: string;
}

interface CompileLogEntry {
  id: string;
  created_at: string;
  status: string;
  auto_accepted: boolean;
  topic_confidence: number | null;
  topic: string;
  template: string;
  vault_path: string | null;
  line: string;
  model: string;
  provider: string;
  duration_ms: number;
}

interface TodayPayload {
  status: {
    feeds_healthy: boolean;
    last_ingest: string | null;
    pending_compilations: number;
    crons_healthy: boolean;
    summary: string;
  };
  cards: {
    incoming: { count: number; items: IncomingItem[] };
    compiled: { count: number; items: CompiledItem[] };
    attention: { count: number; items: AttentionItem[] };
  };
  topic_activity: TopicActivity[];
  compile_log: CompileLogEntry[];
}

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
  return `${d}d ago`;
}

function greetingText(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning, here's the day";
  if (h < 18) return "Good afternoon, here's the day";
  return "Good evening, here's the day";
}

export default function KnowledgeToday() {
  const navigate = useNavigate();
  const [data, setData] = useState<TodayPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch('/api/knowledge/today')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((json: TodayPayload) => {
        if (!cancelled) {
          setData(json);
          setError(null);
        }
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[820px] mx-auto px-6 pt-6 pb-16">
        <p className="font-serif text-[20px] text-text-secondary mb-8">{greetingText()}</p>

        <BootstrapCard />

        {error && (
          <p className="text-red text-[12px]">Couldn't load Today — {error.message}</p>
        )}

        {loading && !data && !error && (
          <div className="flex lg:flex-row flex-col gap-3 mb-10">
            <Skeleton className="flex-1 h-[120px] rounded-2xl" />
            <Skeleton className="flex-1 h-[120px] rounded-2xl" />
            <Skeleton className="flex-1 h-[120px] rounded-2xl" />
          </div>
        )}

        {data && !error && (() => {
          const allZero =
            data.cards.incoming.count === 0 &&
            data.cards.compiled.count === 0 &&
            data.cards.attention.count === 0;

          if (!data.status.feeds_healthy && allZero) {
            return (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <Radio size={24} className="text-text-quaternary mb-3" />
                <p className="text-[12px] text-text-tertiary">
                  Knowledge is quiet. Add sources in Feed → Sources to get started.
                </p>
              </div>
            );
          }

          return (
            <>
              {/* Three cards */}
              <div className="flex lg:flex-row flex-col gap-3 mb-10">
                {/* Incoming */}
                <div className="flex-1 bg-bg-secondary/40 border border-border rounded-2xl p-4 relative">
                  <Radio size={14} className="absolute top-4 right-4 text-text-quaternary" />
                  <div className="flex items-baseline gap-2 mb-3">
                    <h3 className="text-[12px] text-text">Incoming</h3>
                    <span className="font-mono text-[10px] text-text-quaternary">
                      {data.cards.incoming.count}
                    </span>
                  </div>
                  {data.cards.incoming.count === 0 ? (
                    <p className="text-[12px] text-text-quaternary">Nothing new</p>
                  ) : (
                    <ul className="space-y-2">
                      {data.cards.incoming.items.slice(0, 3).map((item) => (
                        <li key={item.id}>
                          <button
                            onClick={() => navigate(`/knowledge/feed?focus=${item.id}`)}
                            className="w-full text-left block"
                          >
                            <span className="inline-block text-accent text-[10px] uppercase tracking-wide mr-2">
                              {item.platform}
                            </span>
                            <span className="text-[12px] text-text truncate block">
                              {item.title}
                            </span>
                            <span className="font-mono text-[10px] text-text-quaternary">
                              {item.relevance_score.toFixed(2)}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                {/* Compiled */}
                <div className="flex-1 bg-bg-secondary/40 border border-border rounded-2xl p-4 relative">
                  <Sparkles size={14} className="absolute top-4 right-4 text-text-quaternary" />
                  <div className="flex items-baseline gap-2 mb-3">
                    <h3 className="text-[12px] text-text">Compiled</h3>
                    <span className="font-mono text-[10px] text-text-quaternary">
                      {data.cards.compiled.count}
                    </span>
                  </div>
                  {data.cards.compiled.count === 0 ? (
                    <p className="text-[12px] text-text-quaternary">Nothing compiled yet</p>
                  ) : (
                    <ul className="space-y-2">
                      {data.cards.compiled.items.slice(0, 3).map((item) => (
                        <li key={item.id}>
                          <span className="inline-block text-accent text-[10px] uppercase tracking-wide mb-1">
                            {item.topic}
                          </span>
                          <p className="text-[12px] text-text-tertiary line-clamp-2">
                            {item.summary}
                          </p>
                          {item.concepts.slice(0, 2).length > 0 && (
                            <div className="flex gap-2 mt-1">
                              {item.concepts.slice(0, 2).map((c) => (
                                <span
                                  key={c}
                                  className="text-text-quaternary text-[10px]"
                                >
                                  {c}
                                </span>
                              ))}
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                {/* Attention */}
                <div className="flex-1 bg-bg-secondary/40 border border-border rounded-2xl p-4 relative">
                  <AlertCircle size={14} className="absolute top-4 right-4 text-text-quaternary" />
                  <div className="flex items-baseline gap-2 mb-3">
                    <h3 className="text-[12px] text-text">Needs attention</h3>
                    <span className="font-mono text-[10px] text-text-quaternary">
                      {data.cards.attention.count}
                    </span>
                  </div>
                  {data.cards.attention.count === 0 ? (
                    <p className="text-[12px] text-text-quaternary">All clear</p>
                  ) : (
                    <ul className="space-y-2">
                      {data.cards.attention.items.slice(0, 3).map((item, idx) => (
                        <li key={idx}>
                          {item.kind === 'pending_proposal' ? (
                            <>
                              <p className="text-[12px] text-text truncate">{item.title}</p>
                              <span className="font-mono text-[10px] text-accent">
                                conf {item.confidence.toFixed(2)}
                              </span>
                            </>
                          ) : (
                            <>
                              <p className="text-[12px] text-text truncate">{item.title}</p>
                              <span className="font-mono text-[10px] text-text-quaternary">
                                orphan · stage {item.stage}
                              </span>
                            </>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>

              {/* Topic activity */}
              <section className="mb-10">
                <h2 className="text-[11px] uppercase tracking-wide text-text-quaternary mb-3">
                  Topic activity
                </h2>
                {data.topic_activity.length === 0 ? (
                  <p className="text-[12px] text-text-quaternary text-center py-6">
                    No topic indexes yet
                  </p>
                ) : (
                  <div className="grid grid-cols-2 gap-2">
                    {data.topic_activity.map((t) => (
                      <div
                        key={t.slug}
                        className="bg-bg-secondary/30 border border-border rounded-lg px-3 py-2.5"
                      >
                        <div className="flex items-baseline justify-between gap-2">
                          <span className="text-[12px] text-text truncate">{t.title}</span>
                          <span className="font-mono text-[10px] text-text-quaternary">
                            {t.doc_count}
                          </span>
                        </div>
                        <p className="text-[11px] text-text-tertiary line-clamp-1">
                          {t.orientation?.slice(0, 80) ?? ''}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              {/* Recent activity */}
              <section>
                <h2 className="text-[11px] uppercase tracking-wide text-text-quaternary mb-3">
                  Recent activity
                </h2>
                {data.compile_log.length === 0 ? (
                  <p className="text-[12px] text-text-quaternary text-center py-6">
                    No recent activity
                  </p>
                ) : (
                  <ul>
                    {data.compile_log.slice(0, 10).map((entry) => (
                      <li
                        key={entry.id}
                        className="border-b border-border py-2 flex items-baseline gap-3"
                      >
                        <span
                          className="font-mono text-[10px] text-text-quaternary shrink-0"
                          style={{ width: 50 }}
                        >
                          {timeAgo(entry.created_at)}
                        </span>
                        <span className="text-[12px] text-text-tertiary flex-1 min-w-0 truncate">
                          {entry.line}
                        </span>
                        <span className="font-mono text-[10px] text-text-quaternary shrink-0">
                          {entry.model}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </>
          );
        })()}
      </div>
    </div>
  );
}
