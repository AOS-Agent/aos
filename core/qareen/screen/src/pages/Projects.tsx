import { FolderKanban, ArrowRight } from 'lucide-react';
import { useWork, type Goal } from '@/hooks/useWork';
import { EmptyState, Tag, SkeletonCards, ErrorBanner } from '@/components/primitives';
import { StatusDot } from '@/components/primitives/StatusDot';
import { areaTone } from '@/lib/areaStyle';

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

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[880px] mx-auto px-6 py-8">
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
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {projects.map(proj => {
              // AUTHORITATIVE counts — fix#2, never derive from the capped tasks[] array.
              const total = proj.task_count ?? 0;
              const done = proj.done_count ?? 0;
              const active = proj.active_count ?? 0;
              const todo = Math.max(0, total - done - active);   // no authoritative waiting count exists

              // fix#3 — resolve goal id -> goal title + area color.
              const goalIdx = goals.findIndex(g => g.id === proj.goal);
              const goal = goalIdx >= 0 ? goals[goalIdx] : undefined;
              const tone = areaTone(proj.goal, goalIdx < 0 ? 0 : goalIdx);

              return (
                <div
                  key={proj.id}
                  onClick={() => onProjectClick?.(proj.id)}
                  className="bg-bg-secondary rounded-[7px] p-5 border border-border-secondary hover:border-border-tertiary hover:bg-bg-tertiary/50 transition-all cursor-pointer group"
                  style={{ transitionDuration: 'var(--duration-instant)' }}
                >
                  {/* Project header */}
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <h3 className="text-[17px] font-[590] text-text tracking-[-0.01em] truncate group-hover:text-text transition-colors">
                          {proj.title}
                        </h3>
                        <ArrowRight className="w-3.5 h-3.5 text-text-quaternary opacity-0 group-hover:opacity-100 transition-opacity shrink-0" style={{ transitionDuration: 'var(--duration-instant)' }} />
                      </div>
                    </div>
                    <Tag label={proj.status} color={statusColor(proj.status)} />
                  </div>

                  {/* Goal — title + area color dot (fix#3) */}
                  {goal && (
                    <div className="flex items-center gap-2 mb-4">
                      <span className={`w-[6px] h-[6px] rounded-full shrink-0 ${tone.dot}`} />
                      <span className={`text-[14px] ${tone.text} truncate`}>{goal.title}</span>
                    </div>
                  )}

                  {/* Progress bar — authoritative totals (fix#2) */}
                  <div className="mb-4">
                    <ProgressBar done={done} total={total} />
                  </div>

                  {/* Task breakdown — authoritative counts */}
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
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
