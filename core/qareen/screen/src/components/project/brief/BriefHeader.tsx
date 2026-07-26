/**
 * The project header — identity, derived state, and the live indicator.
 *
 * The state chip and its reason replace the hand-typed `status` field. `status`
 * was whatever someone typed once; `state` is compiled from when the project
 * actually last moved, and `state_reason` says why in a sentence.
 *
 * Counts stay authoritative — they come from the project record, never from the
 * (page-capped) task rows.
 */

import { GitBranch } from 'lucide-react';
import { timeAgo } from '@/lib/format';
import type { AreaTone } from '@/lib/areaStyle';
import type { ProjectBrief } from '@/hooks/useProjectBrief';
import { activitySourceLabel } from './briefStyle';
import { ProgressRail, StateChip } from './chrome';

export function BriefHeader({
  title,
  goalTitle,
  tone,
  brief,
  total,
  done,
  active,
  justUpdated,
}: {
  title: string;
  goalTitle: string | null;
  tone: AreaTone;
  brief: ProjectBrief | null;
  total: number;
  done: number;
  active: number;
  justUpdated: boolean;
}) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const todoLeft = Math.max(0, total - done - active);
  const source = brief ? activitySourceLabel(brief.last_activity_source) : '';

  return (
    <header className="mb-8">
      {/* Area — resolved to the goal's real title, never its id. */}
      <div className="flex items-center gap-2 mb-1.5">
        <span className={`w-2 h-2 rounded-full shrink-0 ${tone.dot}`} />
        <span className={`text-[13px] ${tone.text} truncate`}>{goalTitle ?? 'No area'}</span>
      </div>

      <div className="flex items-start justify-between gap-6">
        <h1 className="font-serif text-[30px] leading-[1.2] font-[600] text-text tracking-[-0.015em] min-w-0">
          {title}
        </h1>

        {/* Live indicator — fires when a recompile lands over SSE while open. */}
        <span
          className={`shrink-0 flex items-center gap-1.5 text-[11px] mt-2 transition-opacity ${
            justUpdated ? 'text-tag-green opacity-100' : 'text-text-quaternary opacity-60'
          }`}
          style={{ transitionDuration: 'var(--duration-normal)' }}
        >
          <span
            className={`w-[6px] h-[6px] rounded-full ${justUpdated ? 'bg-tag-green animate-pulse' : 'bg-text-quaternary'}`}
          />
          {justUpdated ? 'Updated just now' : 'Live'}
        </span>
      </div>

      {/* Derived state + why + when it last moved. */}
      {brief && (
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 mt-3">
          <StateChip state={brief.state} />
          {brief.state_reason && (
            <span className="text-[13px] text-text-tertiary">{brief.state_reason}</span>
          )}
          {brief.last_activity && (
            <span className="text-[13px] text-text-quaternary">
              · Last moved {timeAgo(brief.last_activity)}
              {source && ` · ${source}`}
            </span>
          )}
        </div>
      )}

      {/* Derived tags — union of task tags, doc frontmatter, and structural flags. */}
      {brief && brief.tags.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 mt-2.5">
          {brief.tags.map(t => (
            <span key={t} className="text-[11px] text-text-quaternary bg-bg-secondary rounded-xs h-[19px] px-1.5 inline-flex items-center">
              #{t}
            </span>
          ))}
        </div>
      )}

      {/* Progress — authoritative counts. */}
      <div className="mt-5 flex items-center gap-2.5">
        <ProgressRail done={done} total={total} barClass={tone.dot} />
        <span className="text-[12px] font-mono text-text-quaternary tabular-nums shrink-0">{done}/{total}</span>
        <span className={`text-[13px] font-mono font-[510] shrink-0 ${tone.text}`}>{pct}%</span>
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-text-quaternary">
        <span>{active} active · {todoLeft} todo · {done} done</span>
        {brief?.done_when && (
          <span className="text-text-tertiary">Done when: {brief.done_when}</span>
        )}
        {brief?.appetite && <span>Appetite: {brief.appetite}</span>}
        {brief?.repo_path && (
          <span className="flex items-center gap-1 font-mono truncate max-w-[320px]" title={brief.repo_path}>
            <GitBranch className="w-3 h-3 shrink-0" />
            {brief.repo_path}
          </span>
        )}
      </div>
    </header>
  );
}
