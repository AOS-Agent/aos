import { useState, useEffect } from 'react';
import { Activity, Database, AlertCircle, Cpu } from 'lucide-react';
import { Skeleton } from '@/components/primitives/Skeleton';

interface CronEntry {
  name: string;
  schedule: string;
  last_run: string | null;
  total_items: number;
  status: 'ok' | 'never_run' | 'stale';
}

interface QueueDepth {
  pending_extraction: number;
  pending_compilation: number;
  pending_review: number;
  orphans: number;
}

interface SourceEntry {
  id: string;
  name: string;
  platform: string;
  is_active: boolean;
  last_checked: string | null;
  last_success: string | null;
  consecutive_failures: number;
  items_total: number;
  healthy: boolean;
}

interface LLMActivity {
  id: string;
  created_at: string;
  status: string;
  auto_accepted: boolean;
  topic_confidence: number | null;
  topic: string;
  template: string;
  model: string;
  provider: string;
  tokens_in: number;
  tokens_out: number;
  duration_ms: number;
}

interface FlowStats {
  sources: number;
  fetched_7d: number;
  stored_7d: number;
  compiled_7d: number;
  saved_7d: number;
}

interface PipelineResponse {
  crons: CronEntry[];
  queues: QueueDepth;
  sources: SourceEntry[];
  llm_activity: LLMActivity[];
  flow: FlowStats;
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

function statusDotClass(status: CronEntry['status']): string {
  if (status === 'ok') return 'bg-green';
  if (status === 'stale') return 'bg-red';
  return 'bg-text-quaternary';
}

function activityStatusBadge(entry: LLMActivity): { label: string; cls: string } {
  if (entry.auto_accepted || entry.status === 'auto_accepted') {
    return { label: 'auto', cls: 'text-green' };
  }
  if (entry.status === 'approved') return { label: 'approved', cls: 'text-accent' };
  if (entry.status === 'rejected') return { label: 'rejected', cls: 'text-red' };
  if (entry.status === 'pending') return { label: 'pending', cls: 'text-yellow' };
  return { label: entry.status, cls: 'text-text-quaternary' };
}

export default function KnowledgePipeline() {
  const [data, setData] = useState<PipelineResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = () => {
      fetch('/api/knowledge/pipeline')
        .then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        })
        .then((json: PipelineResponse) => {
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
    };

    load();
    const interval = setInterval(load, 30000);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const flowStages = data
    ? [
        { label: 'Sources', value: data.flow.sources },
        { label: 'Fetched 7d', value: data.flow.fetched_7d },
        { label: 'Stored 7d', value: data.flow.stored_7d },
        { label: 'Compiled 7d', value: data.flow.compiled_7d },
        { label: 'Saved 7d', value: data.flow.saved_7d },
      ]
    : [];

  const isCold =
    data && data.flow.fetched_7d === 0 && data.crons.length === 0;

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[920px] mx-auto px-6 pt-6 pb-16">
        {error && (
          <p className="text-red text-[12px] mb-6">
            Couldn't load pipeline — {error.message}
          </p>
        )}

        {loading && !data && !error && (
          <div className="space-y-8">
            <div className="flex flex-col md:flex-row gap-3">
              <Skeleton className="flex-1 h-[90px] rounded-2xl" />
              <Skeleton className="flex-1 h-[90px] rounded-2xl" />
              <Skeleton className="flex-1 h-[90px] rounded-2xl" />
            </div>
            <div className="space-y-2">
              <Skeleton className="h-10 w-full rounded" />
              <Skeleton className="h-10 w-full rounded" />
              <Skeleton className="h-10 w-full rounded" />
              <Skeleton className="h-10 w-full rounded" />
            </div>
          </div>
        )}

        {data && !error && isCold && (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Database size={24} className="text-text-quaternary mb-3" />
            <p className="text-[12px] text-text-tertiary">
              Pipeline is cold. Add sources to get started.
            </p>
          </div>
        )}

        {data && !error && !isCold && (
          <>
            {/* Section A — Sources → flow */}
            <section className="mb-10">
              <h2 className="text-[11px] uppercase tracking-wide text-text-quaternary mb-3">
                Sources → flow
              </h2>
              <div className="flex flex-col md:flex-row items-stretch md:items-center gap-2">
                {flowStages.map((stage, idx) => (
                  <div key={stage.label} className="contents md:flex md:items-center md:flex-1">
                    <div className="bg-bg-secondary/30 border border-border rounded-2xl p-4 flex-1">
                      <div className="font-mono text-[28px] text-text leading-none mb-2">
                        {stage.value}
                      </div>
                      <div className="text-[11px] text-text-tertiary uppercase tracking-wide">
                        {stage.label}
                      </div>
                    </div>
                    {idx < flowStages.length - 1 && (
                      <span className="hidden md:inline-block text-text-quaternary px-1 text-[14px]">
                        →
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </section>

            {/* Section B — Crons */}
            <section className="mb-10">
              <h2 className="text-[11px] uppercase tracking-wide text-text-quaternary mb-3 flex items-center gap-2">
                <Activity size={12} />
                Crons
              </h2>
              {data.crons.length === 0 ? (
                <p className="text-[12px] text-text-quaternary py-4">No crons registered</p>
              ) : (
                <ul>
                  {data.crons.map((cron) => (
                    <li
                      key={cron.name}
                      className="border-b border-border py-3 flex items-center gap-4"
                    >
                      <span
                        className={`inline-block w-1.5 h-1.5 rounded-full shrink-0 ${statusDotClass(cron.status)}`}
                      />
                      <span className="text-[13px] text-text flex-1 min-w-0 truncate">
                        {cron.name}
                      </span>
                      <span className="font-mono text-[11px] text-text-tertiary shrink-0 hidden sm:inline">
                        {cron.schedule}
                      </span>
                      <span className="font-mono text-[11px] text-text-tertiary shrink-0 w-[70px] text-right">
                        {timeAgo(cron.last_run)}
                      </span>
                      <span className="font-mono text-[11px] text-text-tertiary shrink-0 w-[60px] text-right">
                        {cron.total_items}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {/* Section C — Queue depth */}
            <section className="mb-10">
              <h2 className="text-[11px] uppercase tracking-wide text-text-quaternary mb-3">
                Queue depth
              </h2>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {[
                  { label: 'Pending extraction', value: data.queues.pending_extraction },
                  { label: 'Pending compilation', value: data.queues.pending_compilation },
                  { label: 'Pending review', value: data.queues.pending_review },
                  { label: 'Orphans', value: data.queues.orphans },
                ].map((q) => {
                  const quiet = q.value === 0;
                  return (
                    <div
                      key={q.label}
                      className={`rounded-2xl p-4 border ${
                        quiet
                          ? 'bg-bg-secondary/30 border-border opacity-60'
                          : 'bg-bg-secondary/30 border-accent/30'
                      }`}
                    >
                      <div
                        className={`font-mono text-[24px] leading-none mb-2 ${
                          quiet ? 'text-text' : 'text-accent'
                        }`}
                      >
                        {quiet ? 'None' : q.value}
                      </div>
                      <div className="text-[11px] text-text-tertiary uppercase tracking-wide">
                        {q.label}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>

            {/* Section D — Source health */}
            <section className="mb-10">
              <h2 className="text-[11px] uppercase tracking-wide text-text-quaternary mb-3 flex items-center gap-2">
                <AlertCircle size={12} />
                Sources
              </h2>
              {data.sources.length === 0 ? (
                <p className="text-[12px] text-text-quaternary py-4">No sources configured</p>
              ) : (
                <ul>
                  {data.sources.map((source) => (
                    <li
                      key={source.id}
                      className={`border-b border-border py-3 flex items-center gap-4 ${
                        source.is_active ? '' : 'opacity-40'
                      }`}
                    >
                      <span
                        className={`inline-block w-1.5 h-1.5 rounded-full shrink-0 ${
                          source.healthy ? 'bg-green' : 'bg-red'
                        }`}
                      />
                      <span className="text-[13px] text-text flex-1 min-w-0 truncate">
                        {source.name}
                      </span>
                      <span className="text-accent text-[10px] uppercase tracking-wide shrink-0 hidden sm:inline">
                        {source.platform}
                      </span>
                      <span className="font-mono text-[11px] text-text-tertiary shrink-0 w-[70px] text-right">
                        {timeAgo(source.last_success)}
                      </span>
                      <span className="font-mono text-[11px] text-text-tertiary shrink-0 w-[60px] text-right">
                        {source.items_total}
                      </span>
                      <span
                        className={`font-mono text-[11px] shrink-0 w-[40px] text-right ${
                          source.consecutive_failures > 0 ? 'text-red' : 'text-transparent'
                        }`}
                      >
                        {source.consecutive_failures > 0 ? `×${source.consecutive_failures}` : '·'}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {/* Section E — Recent LLM activity */}
            <section>
              <h2 className="text-[11px] uppercase tracking-wide text-text-quaternary mb-3 flex items-center gap-2">
                <Cpu size={12} />
                Recent activity
              </h2>
              {data.llm_activity.length === 0 ? (
                <p className="text-[12px] text-text-quaternary py-4">No recent activity</p>
              ) : (
                <div className="max-h-[400px] overflow-y-auto">
                  <ul>
                    {data.llm_activity.slice(0, 20).map((entry) => {
                      const badge = activityStatusBadge(entry);
                      return (
                        <li
                          key={entry.id}
                          className="border-b border-border py-2 flex items-baseline gap-3"
                        >
                          <span
                            className="font-mono text-[10px] text-text-quaternary shrink-0"
                            style={{ width: 60 }}
                          >
                            {timeAgo(entry.created_at)}
                          </span>
                          <span className="text-[12px] text-text-tertiary flex-1 min-w-0 truncate">
                            compiled{' '}
                            <span className="text-text">{entry.template}</span> into{' '}
                            <span className="text-text">{entry.topic}</span>
                          </span>
                          <span className="font-mono text-[10px] text-text-quaternary shrink-0 w-[40px] text-right">
                            {entry.topic_confidence != null
                              ? entry.topic_confidence.toFixed(2)
                              : '—'}
                          </span>
                          <span className="font-mono text-[10px] text-text-quaternary shrink-0 hidden md:inline w-[120px] truncate text-right">
                            {entry.model}/{entry.provider}
                          </span>
                          <span
                            className={`font-mono text-[10px] shrink-0 w-[60px] text-right ${badge.cls}`}
                          >
                            {badge.label}
                          </span>
                          <span className="font-mono text-[10px] text-text-quaternary shrink-0 w-[50px] text-right">
                            {entry.duration_ms}ms
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  );
}
