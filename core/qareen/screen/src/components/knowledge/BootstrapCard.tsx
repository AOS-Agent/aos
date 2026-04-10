import { useState, useEffect, useRef } from 'react';
import { Pause, Play, X as XIcon, ChevronDown, ChevronUp } from 'lucide-react';

interface BootstrapPreview {
  total_docs: number;
  eligible_docs: number;
  by_reason: Record<string, number>;
  by_stage: Record<string, number>;
  sample_docs: Array<{
    path: string;
    title: string;
    stage: number;
    type: string;
    reasons: string[];
    word_count: number;
  }>;
  estimated_cost_usd: number;
  estimated_duration_seconds: number;
  notes: string[];
}

interface BootstrapRun {
  id: string;
  started_at: string;
  ended_at: string | null;
  status: 'pending' | 'running' | 'paused' | 'done' | 'failed' | 'cancelled';
  total_docs: number;
  processed_docs: number;
  skipped_docs: number;
  auto_accepted: number;
  pending_review: number;
  errors: number;
  current_path: string | null;
  git_ref: string | null;
  git_branch: string | null;
  estimated_cost_usd: number | null;
  actual_cost_usd: number | null;
  error_log: Array<{ path: string; error: string }>;
}

const ACTIVE_STATUSES = new Set(['pending', 'running', 'paused']);
const TERMINAL_STATUSES = new Set(['done', 'failed', 'cancelled']);

function formatCost(usd: number | null | undefined): string {
  if (usd == null) return '0.00';
  if (usd < 0.01) return usd.toFixed(4);
  return usd.toFixed(2);
}

function formatDuration(seconds: number): string {
  const m = Math.max(1, Math.round(seconds / 60));
  return `${m}m`;
}

function truncatePath(path: string, max = 52): string {
  if (path.length <= max) return path;
  return '…' + path.slice(-(max - 1));
}

export function BootstrapCard() {
  const [preview, setPreview] = useState<BootstrapPreview | null>(null);
  const [run, setRun] = useState<BootstrapRun | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [fetchFailed, setFetchFailed] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const [showErrors, setShowErrors] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<number | null>(null);

  // Initial fetch: check for active run first, otherwise fetch preview.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const runsRes = await fetch('/api/knowledge/bootstrap/runs');
        if (runsRes.ok) {
          const data = (await runsRes.json()) as { runs: BootstrapRun[] };
          const active = (data.runs || []).find((r) => ACTIVE_STATUSES.has(r.status));
          if (active) {
            if (!cancelled) {
              setRun(active);
              setLoaded(true);
            }
            return;
          }
        }
        const prevRes = await fetch('/api/knowledge/bootstrap/preview');
        if (!prevRes.ok) throw new Error(`HTTP ${prevRes.status}`);
        const prevJson = (await prevRes.json()) as BootstrapPreview;
        if (!cancelled) {
          setPreview(prevJson);
          setLoaded(true);
        }
      } catch {
        if (!cancelled) {
          setFetchFailed(true);
          setLoaded(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Polling while run is active.
  useEffect(() => {
    if (!run) return;
    if (TERMINAL_STATUSES.has(run.status)) {
      if (pollRef.current) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    if (pollRef.current) return;
    const id = window.setInterval(async () => {
      try {
        const res = await fetch(`/api/knowledge/bootstrap/runs/${run.id}`);
        if (!res.ok) return;
        const next = (await res.json()) as BootstrapRun;
        setRun(next);
      } catch {
        /* swallow */
      }
    }, 2000);
    pollRef.current = id;
    return () => {
      window.clearInterval(id);
      pollRef.current = null;
    };
  }, [run?.id, run?.status]);

  async function startBootstrap() {
    if (starting) return;
    setStarting(true);
    try {
      const res = await fetch('/api/knowledge/bootstrap/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const newRun = (await res.json()) as BootstrapRun;
      setRun(newRun);
      setPreview(null);
      setShowPreview(false);
      setShowErrors(false);
      setConfirmCancel(false);
    } catch {
      /* swallow */
    } finally {
      setStarting(false);
    }
  }

  async function controlRun(action: 'pause' | 'resume' | 'cancel') {
    if (!run) return;
    try {
      const res = await fetch(`/api/knowledge/bootstrap/runs/${run.id}/${action}`, {
        method: 'POST',
      });
      if (!res.ok) return;
      const next = (await res.json()) as BootstrapRun;
      setRun(next);
    } catch {
      /* swallow */
    }
  }

  if (!loaded) return null;

  if (fetchFailed && !run && !preview) {
    return <div className="mb-8 text-[11px] text-text-quaternary">Bootstrap unavailable</div>;
  }

  // State 2 + 3: we have a run
  if (run) {
    const isActive = ACTIVE_STATUSES.has(run.status);
    const isTerminal = TERMINAL_STATUSES.has(run.status);

    if (isActive) {
      const pct = run.total_docs > 0 ? Math.min(100, Math.round((run.processed_docs / run.total_docs) * 100)) : 0;
      const shortRef = run.git_ref ? run.git_ref.slice(0, 7) : null;

      return (
        <div className="bg-bg-secondary/40 border border-border rounded-2xl p-5 mb-8">
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <h3 className="text-[14px] text-text">
                  Bootstrap {run.status === 'paused' ? 'paused' : 'running'}
                </h3>
                <span
                  className={`inline-block h-[6px] w-[6px] rounded-full ${
                    run.status === 'running' ? 'bg-accent animate-pulse' : 'bg-text-quaternary'
                  }`}
                />
              </div>
              <p className="font-serif text-[12px] text-text-tertiary mt-1">
                {run.processed_docs} of {run.total_docs} · {pct}%
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {run.status === 'running' && (
                <button
                  onClick={() => controlRun('pause')}
                  className="h-8 px-3 rounded-full bg-bg-tertiary text-[11px] text-text-secondary hover:bg-hover inline-flex items-center gap-1"
                >
                  <Pause className="h-3 w-3" />
                  Pause
                </button>
              )}
              {run.status === 'paused' && (
                <button
                  onClick={() => controlRun('resume')}
                  className="h-8 px-3 rounded-full bg-bg-tertiary text-[11px] text-text-secondary hover:bg-hover inline-flex items-center gap-1"
                >
                  <Play className="h-3 w-3" />
                  Resume
                </button>
              )}
              <button
                onClick={() => {
                  if (confirmCancel) {
                    controlRun('cancel');
                    setConfirmCancel(false);
                  } else {
                    setConfirmCancel(true);
                  }
                }}
                onBlur={() => setConfirmCancel(false)}
                className="h-8 px-3 rounded-full bg-bg-tertiary text-[11px] text-red hover:bg-hover inline-flex items-center gap-1"
              >
                <XIcon className="h-3 w-3" />
                {confirmCancel ? 'Confirm cancel' : 'Cancel'}
              </button>
            </div>
          </div>

          <div className="h-1 bg-bg-tertiary rounded-full overflow-hidden mt-3">
            <div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} />
          </div>

          {run.status === 'running' && run.current_path && (
            <p className="text-[11px] font-mono text-text-quaternary mt-3 truncate">
              Compiling {truncatePath(run.current_path)}
            </p>
          )}

          <div className="flex items-center gap-4 text-[11px] mt-3 font-mono">
            <span className="text-text-tertiary">
              auto-applied <span className="text-text">{run.auto_accepted}</span>
            </span>
            <span className="text-text-tertiary">
              pending <span className="text-text">{run.pending_review}</span>
            </span>
            <span className={run.errors > 0 ? 'text-red' : 'text-text-tertiary'}>
              errors <span className={run.errors > 0 ? 'text-red' : 'text-text'}>{run.errors}</span>
            </span>
            <span className="text-text-tertiary">
              skipped <span className="text-text">{run.skipped_docs}</span>
            </span>
          </div>

          {shortRef && (
            <p className="text-[10px] font-mono text-text-quaternary mt-2">
              snapshot {shortRef}
              {run.git_branch ? ` on ${run.git_branch}` : ''}
            </p>
          )}
        </div>
      );
    }

    if (isTerminal) {
      const visibleErrors = run.error_log?.slice(0, 5) ?? [];
      return (
        <div className="bg-bg-secondary/40 border border-border rounded-2xl p-5 mb-8">
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <h3 className="text-[14px] text-text">Bootstrap {run.status}</h3>
              <p className="font-serif text-[12px] text-text-tertiary mt-1">
                Processed {run.processed_docs} docs · {run.auto_accepted} auto-applied ·{' '}
                {run.pending_review} pending review
                {run.errors > 0 ? ` · ${run.errors} errors` : ''}
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button
                onClick={startBootstrap}
                disabled={starting}
                className="h-8 px-3 rounded-full bg-bg-tertiary text-[11px] text-text-secondary hover:bg-hover disabled:opacity-50"
              >
                Start new bootstrap
              </button>
            </div>
          </div>

          <div className="mt-3 flex items-center gap-3 text-[11px]">
            <a
              href="/knowledge/feed?filter=proposals"
              className="text-accent hover:text-accent-hover"
            >
              Review pending proposals →
            </a>
            {run.errors > 0 && (
              <button
                onClick={() => setShowErrors((v) => !v)}
                className="text-text-tertiary hover:text-text-secondary inline-flex items-center gap-1"
              >
                {showErrors ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                {showErrors ? 'Hide' : 'Show'} errors
              </button>
            )}
          </div>

          {showErrors && visibleErrors.length > 0 && (
            <div className="mt-3 pt-3 border-t border-border space-y-1.5">
              {visibleErrors.map((e, i) => (
                <div key={i} className="text-[11px] font-mono">
                  <span className="text-text-quaternary">{truncatePath(e.path, 40)}</span>
                  <span className="text-red ml-2">{e.error}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      );
    }
  }

  // State 1: Idle with preview
  if (!preview || preview.eligible_docs === 0) return null;

  return (
    <div className="bg-bg-secondary/40 border border-border rounded-2xl p-5 mb-8">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <h3 className="text-[14px] text-text">Vault bootstrap available</h3>
          <p className="font-serif text-[12px] text-text-tertiary mt-1">
            Found {preview.eligible_docs} documents that can be enriched. Estimated cost ~$
            {formatCost(preview.estimated_cost_usd)}, ~{formatDuration(preview.estimated_duration_seconds)}.
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => setShowPreview((v) => !v)}
            className="h-8 px-3 rounded-full bg-bg-tertiary text-[11px] text-text-secondary hover:bg-hover inline-flex items-center gap-1"
          >
            {showPreview ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            Preview
          </button>
          <button
            onClick={startBootstrap}
            disabled={starting}
            className="h-8 px-3 rounded-full bg-accent text-white text-[11px] font-[520] hover:bg-accent-hover disabled:opacity-50"
          >
            {starting ? 'Starting…' : 'Start bootstrap'}
          </button>
        </div>
      </div>

      {showPreview && (
        <div className="mt-4 pt-4 border-t border-border space-y-3">
          <div>
            <p className="text-[10px] uppercase tracking-wide text-text-quaternary mb-1.5">By stage</p>
            <div className="flex flex-wrap gap-2">
              {Object.entries(preview.by_stage).map(([stage, count]) => (
                <span
                  key={stage}
                  className="inline-flex items-center gap-1.5 h-6 px-2.5 rounded-full bg-bg-tertiary text-[11px] font-mono text-text-secondary"
                >
                  stage {stage} <span className="text-text-quaternary">·</span>{' '}
                  <span className="text-text">{count}</span>
                </span>
              ))}
            </div>
          </div>

          <div>
            <p className="text-[10px] uppercase tracking-wide text-text-quaternary mb-1.5">By reason</p>
            <div className="flex flex-wrap gap-2">
              {Object.entries(preview.by_reason).map(([reason, count]) => (
                <span
                  key={reason}
                  className="inline-flex items-center gap-1.5 h-6 px-2.5 rounded-full bg-bg-tertiary text-[11px] font-mono text-text-secondary"
                >
                  {reason} <span className="text-text-quaternary">·</span>{' '}
                  <span className="text-text">{count}</span>
                </span>
              ))}
            </div>
          </div>

          {preview.sample_docs.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wide text-text-quaternary mb-1.5">
                Sample documents
              </p>
              <div className="space-y-1">
                {preview.sample_docs.slice(0, 5).map((doc) => (
                  <div
                    key={doc.path}
                    className="flex items-center justify-between gap-3 text-[11px] py-1"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="text-text-secondary truncate">{doc.title}</div>
                      <div className="font-mono text-text-quaternary truncate text-[10px]">
                        {truncatePath(doc.path, 60)}
                      </div>
                    </div>
                    <div className="font-mono text-text-quaternary text-[10px] shrink-0">
                      stage {doc.stage} · {doc.word_count}w
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
