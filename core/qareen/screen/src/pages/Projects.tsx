import { FolderKanban, ArrowRight } from 'lucide-react';
import { useWork } from '@/hooks/useWork';
import { EmptyState, Tag, SkeletonCards, ErrorBanner } from '@/components/primitives';
import { StatusDot } from '@/components/primitives/StatusDot';

function ProgressBar({ done, total }: { done: number; total: number }) {
  if (total === 0) {
    return (
      <div className="flex items-center gap-2.5">
        <div className="flex-1 h-1 bg-bg-tertiary rounded-full overflow-hidden" />
        <span className="text-[10px] font-mono text-text-quaternary">0/0</span>
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
      <span className="text-[10px] font-mono text-text-quaternary tabular-nums">{pct}%</span>
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

export default function ProjectsPage() {
  const { data, isLoading, isError } = useWork();

  const projects = data?.projects ?? [];
  const tasks = data?.tasks ?? [];

  return (
    <div className="h-full overflow-y-auto bg-bg">
      <div className="px-6 md:px-8 pt-14 pb-6 max-w-[1200px] mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-4">
            <h1 className="text-[22px] font-[680] text-text tracking-[-0.025em] leading-none">Projects</h1>
            {!isLoading && <span className="text-[11px] text-text-quaternary font-mono">{projects.length}</span>}
          </div>
        </div>

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
              const projTasks = tasks.filter(t => t.project === proj.id || t.project === proj.title);
              const doneTasks = projTasks.filter(t => t.status === 'done');
              const activeTasks = projTasks.filter(t => t.status === 'active');
              const waitingTasks = projTasks.filter(t => t.status === 'waiting');
              const todoTasks = projTasks.filter(t => t.status === 'todo');

              return (
                <div
                  key={proj.id}
                  className="bg-bg-secondary rounded-[7px] p-5 border border-border-secondary hover:border-border-tertiary hover:bg-bg-tertiary/50 transition-all cursor-pointer group"
                  style={{ transitionDuration: 'var(--duration-instant)' }}
                >
                  {/* Project header */}
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <h3 className="text-[15px] font-[590] text-text tracking-[-0.01em] truncate group-hover:text-text transition-colors">
                          {proj.title}
                        </h3>
                        <ArrowRight className="w-3.5 h-3.5 text-text-quaternary opacity-0 group-hover:opacity-100 transition-opacity shrink-0" style={{ transitionDuration: 'var(--duration-instant)' }} />
                      </div>
                    </div>
                    <Tag label={proj.status} color={statusColor(proj.status)} />
                  </div>

                  {/* Goal */}
                  {proj.goal && (
                    <p
                      className="text-[13px] text-text-tertiary mb-4 line-clamp-2 leading-[1.55]"
                      style={{ fontFamily: 'var(--font-serif)' }}
                    >
                      {proj.goal}
                    </p>
                  )}

                  {/* Progress bar */}
                  <div className="mb-4">
                    <ProgressBar done={doneTasks.length} total={projTasks.length} />
                  </div>

                  {/* Task breakdown */}
                  <div className="flex items-center gap-4 text-[10px] text-text-quaternary">
                    <span className="flex items-center gap-1.5">
                      <StatusDot color="gray" size="sm" />
                      {todoTasks.length} todo
                    </span>
                    <span className="flex items-center gap-1.5">
                      <StatusDot color="blue" size="sm" />
                      {activeTasks.length} active
                    </span>
                    {waitingTasks.length > 0 && (
                      <span className="flex items-center gap-1.5">
                        <StatusDot color="yellow" size="sm" />
                        {waitingTasks.length} waiting
                      </span>
                    )}
                    <span className="flex items-center gap-1.5">
                      <StatusDot color="green" size="sm" />
                      {doneTasks.length} done
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
