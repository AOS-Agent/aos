/**
 * "Where this stands" — the most important thing on the page.
 *
 * Renders the agent-written `narrative` when there is one, otherwise the
 * deterministic `summary`. Never blank: the contract guarantees `summary` is
 * always present, so this section always says something true.
 *
 * Serif, generous measure, real typographic weight — this is the qareen
 * speaking, not a stat readout.
 */

import { AlertCircle } from 'lucide-react';
import { timeAgo } from '@/lib/format';
import type { ProjectBrief } from '@/hooks/useProjectBrief';
import { Overline } from './chrome';

export function Narrative({ brief }: { brief: ProjectBrief }) {
  const written = brief.narrative?.trim();
  const text = written || brief.summary?.trim() || '';
  if (!text) return null;

  const paragraphs = text.split(/\n{2,}/).map(p => p.trim()).filter(Boolean);
  const aged = !!written && brief.narrative_aged;

  return (
    <section className="mb-10">
      <Overline>Where this stands</Overline>

      <div className={`max-w-[70ch] ${aged ? 'opacity-80' : ''}`}>
        {paragraphs.map((p, i) => (
          <p
            key={i}
            className="font-serif text-[19px] leading-[1.62] text-text-secondary tracking-[-0.003em] mb-3 last:mb-0 whitespace-pre-line"
          >
            {p}
          </p>
        ))}
      </div>

      {aged && (
        <p className="flex items-center gap-1.5 mt-3 text-[11px] text-text-quaternary">
          <AlertCircle className="w-3 h-3 shrink-0" />
          Written {timeAgo(brief.narrative_written_at)} — the project has moved since.
        </p>
      )}

      {brief.sources.length > 0 && (
        <p className="mt-3 text-[11px] text-text-quaternary">
          Compiled {timeAgo(brief.compiled_at)} from {brief.sources.length}{' '}
          {brief.sources.length === 1 ? 'source' : 'sources'}
          {brief.compile_ms > 0 && <span className="font-mono tabular-nums"> · {brief.compile_ms}ms</span>}
        </p>
      )}
    </section>
  );
}
