import { FolderKanban } from 'lucide-react';
import { useWork } from '@/hooks/useWork';
import { EmptyState, Tag, SectionHeader, Skeleton, SkeletonCards, ErrorBanner } from '@/components/primitives';

function ProgressBar({ done, total }: { done: number; total: number }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-bg-tertiary rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${pct >= 100 ? 'bg-green' : 'bg-accent'}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] font-mono text-text-quaternary">{done}/{total}</span>
    </div>
  );
}

export default function ProjectsPage() {
  const { data, isLoading, isError } = useWork();

  const projects = data?.projects ?? [];
  const tasks = data?.tasks ?? [];

  return (
    <div className="px-5 md:px-8 py-4 md:py-6 overflow-y-auto h-full">
      <h1 className="type-title mb-6">Projects</h1>

      {isError && <ErrorBanner />}

      {isLoading ? (
        <SkeletonCards count={4} />
      ) : projects.length === 0 ? (
        <EmptyState icon={<FolderKanban />} title="No projects" description="Projects will appear here as they are created in the work system." />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {projects.map(proj => {
            const projTasks = tasks.filter(t => t.project === proj.id || t.project === proj.title);
            const doneTasks = projTasks.filter(t => t.status === 'done');
            const activeTasks = projTasks.filter(t => t.status === 'active' || t.status === 'waiting');

            return (
              <div key={proj.id} className="bg-bg-secondary rounded-[7px] p-5 border border-border hover:bg-bg-tertiary transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-[15px] font-[590] text-text tracking-[-0.01em]">{proj.title}</h3>
                  <Tag label={proj.status} color={proj.status === 'active' ? 'green' : proj.status === 'completed' ? 'blue' : 'gray'} />
                </div>
                {proj.goal && <p className="text-[12px] text-text-tertiary mb-3 line-clamp-2">{proj.goal}</p>}
                <ProgressBar done={doneTasks.length} total={projTasks.length} />
                <div className="flex items-center gap-3 mt-3 text-[10px] text-text-quaternary">
                  <span>{projTasks.length} tasks</span>
                  <span>{activeTasks.length} active</span>
                  <span>{doneTasks.length} done</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
