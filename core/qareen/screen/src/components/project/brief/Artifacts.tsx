/**
 * Artifacts — everything this project produced.
 *
 * Initiative docs, specs, decisions, councils, sessions, commits, files. The
 * point is browsability: a filter row across the natural groupings, then a
 * dense grid. Vault documents under `knowledge/` open in a reader; everything
 * else expands in place to show its excerpt and full path.
 */

import { useMemo, useState } from 'react';
import { BookOpen, ChevronDown } from 'lucide-react';
import { formatDate } from '@/lib/format';
import type { Artifact } from '@/hooks/useProjectBrief';
import { ARTIFACT_GROUPS, artifactLabel, artifactTone, vaultDocPath } from './briefStyle';
import { KindChip, Overline } from './chrome';
import { ArtifactDrawer } from './ArtifactDrawer';

function ArtifactCard({ artifact, onRead }: { artifact: Artifact; onRead: (path: string) => void }) {
  const [open, setOpen] = useState(false);
  const doc = vaultDocPath(artifact.path);
  const tone = artifactTone(artifact.kind);

  return (
    <div className="bg-bg-secondary/50 hover:bg-bg-secondary border border-border rounded-[7px] transition-colors"
         style={{ transitionDuration: 'var(--duration-instant)' }}>
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full text-left flex items-start gap-2.5 px-3.5 py-2.5 cursor-pointer"
      >
        <span className="shrink-0 mt-[1px]">
          <KindChip label={artifactLabel(artifact.kind)} tone={tone} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[14px] text-text-secondary truncate">{artifact.title}</span>
          {!open && artifact.excerpt && (
            <span className="block font-serif text-[13px] text-text-quaternary truncate mt-0.5">
              {artifact.excerpt}
            </span>
          )}
        </span>
        {artifact.date && (
          <span className="shrink-0 text-[11px] text-text-quaternary tabular-nums mt-[3px]">
            {formatDate(artifact.date, 'MMM d')}
          </span>
        )}
        <ChevronDown
          className={`shrink-0 w-3.5 h-3.5 text-text-quaternary mt-[3px] transition-transform ${open ? 'rotate-180' : ''}`}
          style={{ transitionDuration: 'var(--duration-instant)' }}
        />
      </button>

      {open && (
        <div className="px-3.5 pb-3 pl-[74px]">
          {artifact.excerpt && (
            <p className="font-serif text-[14px] leading-[1.55] text-text-tertiary mb-2">{artifact.excerpt}</p>
          )}
          <p className="text-[11px] font-mono text-text-quaternary break-all">{artifact.path}</p>
          {doc && (
            <button
              onClick={() => onRead(doc)}
              className="mt-2.5 inline-flex items-center gap-1.5 h-7 px-2.5 rounded-full bg-accent text-on-accent text-[12px] font-[510] cursor-pointer hover:bg-accent-hover transition-colors"
              style={{ transitionDuration: 'var(--duration-instant)' }}
            >
              <BookOpen className="w-3 h-3" />
              Read
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export function Artifacts({ artifacts }: { artifacts: Artifact[] }) {
  const [filter, setFilter] = useState<string>('all');
  const [reading, setReading] = useState<{ path: string; title: string } | null>(null);

  const tabs = useMemo(() => {
    const present = ARTIFACT_GROUPS
      .map(g => ({ ...g, count: artifacts.filter(a => g.kinds.includes(a.kind)).length }))
      .filter(g => g.count > 0);
    // Anything the contract adds later still shows up, under its own kind.
    const known = new Set(ARTIFACT_GROUPS.flatMap(g => g.kinds));
    const extra = [...new Set(artifacts.map(a => a.kind).filter(k => !known.has(k)))]
      .map(k => ({ key: k, label: artifactLabel(k), kinds: [k], count: artifacts.filter(a => a.kind === k).length }));
    return [...present, ...extra];
  }, [artifacts]);

  if (artifacts.length === 0) return null;

  const active = tabs.find(t => t.key === filter);
  const shown = active ? artifacts.filter(a => active.kinds.includes(a.kind)) : artifacts;

  return (
    <section className="mb-10">
      <Overline count={artifacts.length}>Artifacts</Overline>

      {tabs.length > 1 && (
        <div className="flex flex-wrap items-center gap-1.5 mb-3">
          {[{ key: 'all', label: 'All', count: artifacts.length }, ...tabs].map(t => (
            <button
              key={t.key}
              onClick={() => setFilter(t.key)}
              className={`h-7 px-3 rounded-full text-[12px] font-[510] cursor-pointer transition-colors ${
                filter === t.key
                  ? 'bg-accent-subtle text-accent'
                  : 'text-text-tertiary hover:text-text-secondary hover:bg-bg-secondary'
              }`}
              style={{ transitionDuration: 'var(--duration-instant)' }}
            >
              {t.label}
              <span className="ml-1.5 font-mono text-[11px] tabular-nums opacity-60">{t.count}</span>
            </button>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 2xl:grid-cols-3 gap-2 items-start">
        {shown.map((a, i) => (
          <ArtifactCard
            key={`${a.path}-${i}`}
            artifact={a}
            onRead={path => setReading({ path, title: a.title })}
          />
        ))}
      </div>

      {reading && (
        <ArtifactDrawer key={reading.path} path={reading.path} title={reading.title} onClose={() => setReading(null)} />
      )}
    </section>
  );
}
