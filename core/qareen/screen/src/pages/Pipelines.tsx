import { useState } from 'react';
import { GitBranch, Play, Clock, CheckCircle2, XCircle, Loader2 } from 'lucide-react';
import { usePipelines, usePipelineRuns } from '@/hooks/usePipelines';
import { EmptyState, Tag, StatusDot, SectionHeader, Skeleton, SkeletonRows, ErrorBanner } from '@/components/primitives';
import { PipelineStage } from '@/lib/types';
import type { PipelineDefinitionSchema, PipelineRunResponse, PipelineStageSchema } from '@/lib/types';

function stageColor(status: PipelineStage): 'green' | 'red' | 'yellow' | 'blue' | 'gray' {
  switch (status) {
    case PipelineStage.COMPLETED: return 'green';
    case PipelineStage.FAILED: return 'red';
    case PipelineStage.PROCESSING: return 'blue';
    case PipelineStage.ESCALATED: return 'yellow';
    default: return 'gray';
  }
}

function timeAgo(iso: string | undefined): string {
  if (!iso) return '\u2014';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function duration(start: string, end?: string): string {
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  const diff = Math.floor((e - s) / 1000);
  if (diff < 60) return `${diff}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  return `${Math.floor(diff / 3600)}h ${Math.floor((diff % 3600) / 60)}m`;
}

function PipelineCard({ pipeline, selected, onClick }: { pipeline: PipelineDefinitionSchema; selected: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left bg-bg-secondary rounded-[7px] p-4 border transition-colors ${selected ? 'border-accent/30' : 'border-border hover:bg-bg-tertiary'}`}
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      <div className="flex items-center gap-2 mb-1">
        <GitBranch className="w-3.5 h-3.5 text-text-quaternary" />
        <span className="text-[13px] font-[510] text-text-secondary">{pipeline.name}</span>
      </div>
      {pipeline.description && <p className="text-[11px] text-text-quaternary mb-2">{pipeline.description}</p>}
      <div className="flex items-center gap-2">
        <Tag label={`${pipeline.stages.length} stages`} color="gray" />
      </div>
    </button>
  );
}

function RunRow({ run }: { run: PipelineRunResponse }) {
  return (
    <div className="flex items-center gap-3 px-3 py-2.5 rounded-sm hover:bg-hover transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
      <StatusDot color={stageColor(run.status)} size="md" pulse={run.status === PipelineStage.PROCESSING} />
      <span className="text-[12px] font-mono text-text-quaternary w-16 shrink-0">{run.id.slice(0, 8)}</span>
      <Tag label={run.status} color={stageColor(run.status) as 'green' | 'red' | 'yellow' | 'blue' | 'gray'} />
      <div className="flex-1 flex items-center gap-1">
        {run.stages.map((s: PipelineStageSchema, i: number) => (
          <div key={i} className="flex items-center gap-0.5">
            <span className={`w-1.5 h-1.5 rounded-full bg-${stageColor(s.status)}`} />
            {i < run.stages.length - 1 && <span className="w-2 h-px bg-border" />}
          </div>
        ))}
      </div>
      <span className="text-[10px] text-text-quaternary shrink-0">{duration(run.started_at, run.completed_at)}</span>
      <span className="text-[10px] text-text-quaternary shrink-0">{timeAgo(run.started_at)}</span>
    </div>
  );
}

export default function PipelinesPage() {
  const { data, isLoading, isError } = usePipelines();
  const [selectedPipeline, setSelectedPipeline] = useState<string | null>(null);
  const { data: runsData, isLoading: runsLoading } = usePipelineRuns(selectedPipeline);

  return (
    <div className="px-5 md:px-8 py-4 md:py-6 overflow-y-auto h-full">
      {isError && <ErrorBanner message="Failed to load pipelines." />}

      {isLoading ? (
        <SkeletonRows count={3} />
      ) : !data || !Array.isArray(data?.pipelines) || data.pipelines.length === 0 ? (
        <EmptyState icon={<GitBranch />} title="No pipelines defined" description="Workflow pipelines will appear here when configured." />
      ) : (
        <>
          <SectionHeader label="Definitions" count={data.pipelines.length} />
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mb-8">
            {data.pipelines.map(p => (
              <PipelineCard key={p.id} pipeline={p} selected={selectedPipeline === p.name} onClick={() => setSelectedPipeline(selectedPipeline === p.name ? null : p.name)} />
            ))}
          </div>

          {selectedPipeline && (
            <div>
              <SectionHeader label={`${selectedPipeline} Runs`} />
              {runsLoading ? (
                <SkeletonRows count={4} />
              ) : !runsData || !Array.isArray(runsData?.runs) || runsData.runs.length === 0 ? (
                <p className="text-[11px] text-text-quaternary py-4 text-center">No runs yet.</p>
              ) : (
                <div className="space-y-px">
                  {runsData.runs.map(run => <RunRow key={run.id} run={run} />)}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
