/**
 * Projects — the list surface.
 *
 * Cards lead with DERIVED state, not the hand-typed `status` field. `status`
 * was whatever someone typed when the project was created; `state` is compiled
 * from when the project actually last moved. The list should tell the operator
 * what is alive without opening anything.
 *
 * Briefs load per project and share the `['project-brief', id]` cache with
 * ProjectDetail — so this list warms the detail page, and the SSE
 * `project.brief.updated` listener keeps both live. If the brief endpoint isn't
 * available, cards fall back to the static status tag.
 */

import { FolderKanban, ArrowRight } from 'lucide-react';
import { useWork, type Goal } from '@/hooks/useWork';
import { useProjectBriefs } from '@/hooks/useProjectBrief';
import { EmptyState, Tag, SkeletonCards, ErrorBanner } from '@/components/primitives';
import { StatusDot } from '@/components/primitives/StatusDot';
import { StateChip } from '@/components/project/brief/chrome';
import { activitySourceLabel } from '@/components/project/brief/briefStyle';
import { areaTone } from '@/lib/areaStyle';
import { timeAgo } from '@/lib/format';

function ProgressBar({ done, total }: { done: number; total: number }) {
  if (total === 0) {
    return (
      <div className="flex items-center gap-2.5">
        <div className="flex-1 h-1 bg-bg-tertiary rounded-full overflow-hidden" />
        <span className="text-[12px] font-mono text-text-quaternary">0/0</span>
      </div>
    );
  }
  const pct = Math.round((done / total) * 100);
  return (
    <div className="flex items-center gap-2.5">
      <div className="flex-1 h-1 bg-bg-tertiary rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${pct >= 100 ? 'bg-green' : 'bg-accent'}`}
          style={{ width: `${pct}%`, transitionDuration: 'var(--duration-normal)' }}
        />
      </div>
      <span className="text-[12px] font-mono text-text-quaternary tabular-nums">{pct}%</span>
    </div>
  );
}

function statusColor(status: string): 'green' | 'blue' | 'gray' | 'yellow' {
  switch (status) {
    case 'active': return 'green';
    case 'completed': return 'blue';
    case 'paused': return 'yellow';
    default: return 'gray';
  }
}

export default function ProjectsPage({ onProjectClick }: { onProjectClick?: (projectId: string) => void } = {}) {
  const { data, isLoading, isError } = useWork();

  const projects = data?.projects ?? [];
  const goals = (data?.goals ?? []) as Goal[];
  const { byId: briefs } = useProjectBriefs(projects.map(p => p.id));

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[1320px] mx-auto px-6 py-8">
        {!isLoading && projects.length > 0 && (
          <p className="text-[13px] text-text-quaternary font-mono mb-4">{projects.length}</p>
        )}

        {isError && <div className="mb-4"><ErrorBanner /></div>}

        {isLoading ? (
          <SkeletonCards count={4} />
        ) : projects.length === 0 ? (
          <EmptyState
            icon={<FolderKanban />}
            title="No projects yet"
            description="Projects appear here when created through the work system. Each project groups related tasks together."
          />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {projects.map(proj => {
              const brief = briefs[proj.id];

              // AUTHORITATIVE counts — the brief when compiled, else the project
              // record. Never the capped tasks[] array.
              const total = brief?.task_count ?? proj.task_count ?? 0;
              const done = brief?.done_count ?? proj.done_count ?? 0;
              const active = brief?.active_count ?? proj.active_count ?? 0;
              const todo = brief?.todo_count ?? Math.max(0, total - done - active);

              // Resolve goal id -> goal title + area color.
              const goalIdx = goals.findIndex(g => g.id === proj.goal);
              const goal = goalIdx >= 0 ? goals[goalIdx] : undefined;
              const tone = areaTone(proj.goal, goalIdx < 0 ? 0 : goalIdx);

              const source = brief ? activitySourceLabel(brief.last_activity_source) : '';

              return (
                <div
                  key={proj.id}
                  onClick={() => onProjectClick?.(proj.id)}
                  className="bg-bg-secondary rounded-[7px] p-5 border border-border-secondary hover:border-border-tertiary hover:bg-bg-tertiary/50 transition-all cursor-pointer group flex flex-col"
                  style={{ transitionDuration: 'var(--duration-instant)' }}
                >
                  {/* Header — title + derived state (falls back to the typed status). */}
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <h3 className="text-[17px] font-[590] text-text tracking-[-0.01em] truncate">
                        {proj.title}
                      </h3>
                      <ArrowRight className="w-3.5 h-3.5 text-text-quaternary opacity-0 group-hover:opacity-100 transition-opacity shrink-0" style={{ transitionDuration: 'var(--duration-instant)' }} />
                    </div>
                    <span className="shrink-0">
                      {brief
                        ? <StateChip state={brief.state} size="sm" />
                        : <Tag label={proj.status} color={statusColor(proj.status)} />}
                    </span>
                  </div>

                  {/* Why it's in that state — the line that makes the list readable. */}
                  {brief?.state_reason && (
                    <p className="text-[12px] text-text-tertiary leading-[1.45] line-clamp-2 mb-2">
                      {brief.state_reason}
                    </p>
                  )}

                  {/* Goal — title + area color dot. */}
                  {goal && (
                    <div className="flex items-center gap-2 mb-3">
                      <span className={`w-[6px] h-[6px] rounded-full shrink-0 ${tone.dot}`} />
                      <span className={`text-[13px] ${tone.text} truncate`}>{goal.title}</span>
                    </div>
                  )}

                  <div className="mt-auto">
                    <div className="mb-3">
                      <ProgressBar done={done} total={total} />
                    </div>

                    <div className="flex items-center gap-4 text-[12px] text-text-quaternary">
                      <span className="flex items-center gap-1.5">
                        <StatusDot color="gray" size="sm" />
                        {todo} todo
                      </span>
                      <span className="flex items-center gap-1.5">
                        <StatusDot color="blue" size="sm" />
                        {active} active
                      </span>
                      <span className="flex items-center gap-1.5">
                        <StatusDot color="green" size="sm" />
                        {done} done
                      </span>
                    </div>

                    {brief?.last_activity && (
                      <p className="text-[11px] text-text-quaternary mt-2">
                        Last moved {timeAgo(brief.last_activity)}{source && ` · ${source}`}
                      </p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
