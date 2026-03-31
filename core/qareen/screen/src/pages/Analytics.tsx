import { BarChart3, Target, TrendingUp } from 'lucide-react';
import { useWork } from '@/hooks/useWork';
import { useMetrics } from '@/hooks/useMetrics';
import { EmptyState, SectionHeader, Skeleton, SkeletonCards, ErrorBanner, Tag } from '@/components/primitives';
import type { GoalResponse, KeyResultSchema } from '@/lib/types';

function ProgressBar({ current, target }: { current: number; target: number }) {
  const pct = target > 0 ? Math.min(100, Math.round((current / target) * 100)) : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-bg-tertiary rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${pct >= 100 ? 'bg-green' : pct >= 50 ? 'bg-accent' : 'bg-yellow'}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] font-mono text-text-quaternary w-8 text-right">{pct}%</span>
    </div>
  );
}

function GoalCard({ goal }: { goal: GoalResponse }) {
  const totalProgress = goal.key_results.length > 0
    ? goal.key_results.reduce((sum, kr) => sum + (kr.target > 0 ? kr.current / kr.target : 0), 0) / goal.key_results.length
    : 0;

  return (
    <div className="bg-bg-secondary rounded-[7px] p-5 border border-border">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-[15px] font-[590] text-text tracking-[-0.01em]">{goal.title}</h3>
        <Tag label={goal.status} color={goal.status === 'active' ? 'green' : goal.status === 'completed' ? 'blue' : 'gray'} />
      </div>
      {goal.description && <p className="text-[12px] text-text-tertiary mb-3">{goal.description}</p>}
      <ProgressBar current={Math.round(totalProgress * 100)} target={100} />
      {goal.key_results.length > 0 && (
        <div className="mt-4 space-y-2">
          {goal.key_results.map((kr: KeyResultSchema) => (
            <div key={kr.id}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[12px] text-text-secondary">{kr.title}</span>
                <span className="text-[10px] font-mono text-text-quaternary">{kr.current}/{kr.target}{kr.unit ? ` ${kr.unit}` : ''}</span>
              </div>
              <ProgressBar current={kr.current} target={kr.target} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function MetricCard({ metric }: { metric: { name: string; description?: string; data_points: { value: number }[]; unit?: string } }) {
  const latest = metric.data_points[metric.data_points.length - 1];
  const prev = metric.data_points.length > 1 ? metric.data_points[metric.data_points.length - 2] : null;
  const trending = prev ? (latest.value > prev.value ? 'up' : latest.value < prev.value ? 'down' : 'flat') : 'flat';

  return (
    <div className="bg-bg-secondary rounded-[7px] p-5 border border-border">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-[13px] font-[510] text-text-secondary">{metric.name}</h3>
        <TrendingUp className={`w-3.5 h-3.5 ${trending === 'up' ? 'text-green' : trending === 'down' ? 'text-red rotate-180' : 'text-text-quaternary'}`} />
      </div>
      <div className="text-[22px] font-[680] text-text tracking-[-0.025em]">
        {latest?.value ?? '\u2014'}
        {metric.unit && <span className="text-[12px] font-[400] text-text-quaternary ml-1">{metric.unit}</span>}
      </div>
      {metric.description && <p className="text-[11px] text-text-quaternary mt-1">{metric.description}</p>}
    </div>
  );
}

export default function AnalyticsPage() {
  const { data: workData, isLoading: workLoading } = useWork();
  const { data: metricsData, isLoading: metricsLoading, isError } = useMetrics();

  const goals = workData?.goals ?? [];

  return (
    <div className="px-5 md:px-8 py-4 md:py-6 overflow-y-auto h-full">
      <h1 className="type-title mb-6">Analytics</h1>

      {isError && <ErrorBanner message="Failed to load metrics data." />}

      {/* Goals */}
      <div className="mb-10">
        <SectionHeader label="Goals" icon={<Target />} count={goals.length} />
        {workLoading ? <SkeletonCards count={2} /> : goals.length === 0 ? (
          <EmptyState icon={<Target />} title="No goals tracked" description="Goals will appear here when configured in the work system." />
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {(goals as GoalResponse[]).map(g => <GoalCard key={g.id} goal={g} />)}
          </div>
        )}
      </div>

      {/* Metrics */}
      <div>
        <SectionHeader label="Metrics" icon={<BarChart3 />} />
        {metricsLoading ? <SkeletonCards count={3} /> : !metricsData || !Array.isArray(metricsData?.metrics) || metricsData.metrics.length === 0 ? (
          <EmptyState icon={<BarChart3 />} title="No metrics tracked yet" description="Metrics will appear here as KPIs are defined and data flows in." />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {metricsData.metrics.map(m => <MetricCard key={m.name} metric={m} />)}
          </div>
        )}
      </div>
    </div>
  );
}
