import { useEffect, useState } from 'react';

// ---------------------------------------------------------------------------
// KnowledgeStatusStrip
//
// Thin one-line system status at the top of every Knowledge view.
// Polls /api/knowledge/health every 30 seconds. Green dot + summary.
// ---------------------------------------------------------------------------

interface HealthPayload {
  healthy?: boolean;
  feeds_healthy?: boolean;
  last_ingest?: string | null;
  pending_compilations?: number;
  crons_healthy?: boolean;
  summary?: string;
}

export function KnowledgeStatusStrip() {
  const [health, setHealth] = useState<HealthPayload | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      fetch('/api/knowledge/health')
        .then((r) => r.json())
        .then((data) => {
          if (alive) setHealth(data);
        })
        .catch(() => {});
    };
    load();
    const id = setInterval(load, 30000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const healthy = health?.feeds_healthy ?? false;
  const summary = health?.summary ?? 'Loading…';
  const pending = health?.pending_compilations ?? 0;

  return (
    <div className="shrink-0 h-10 px-6 flex items-center gap-3 border-b border-border">
      <span
        className={`inline-block w-1.5 h-1.5 rounded-full ${
          healthy ? 'bg-green' : 'bg-text-quaternary'
        }`}
        style={{
          boxShadow: healthy ? '0 0 6px var(--color-green)' : undefined,
        }}
      />
      <span className="text-[11px] text-text-tertiary font-mono">{summary}</span>
      {pending > 0 && (
        <span className="text-[10px] text-accent bg-accent-subtle rounded-full px-2 py-0.5 font-[520]">
          {pending} pending review
        </span>
      )}
    </div>
  );
}
