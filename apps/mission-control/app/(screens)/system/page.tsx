'use client';

import { Activity, Terminal } from 'lucide-react';
import { useServices } from '@/hooks/useServices';
import { useCrons } from '@/hooks/useCrons';
import { SkeletonRows, SkeletonCards } from '@/components/primitives/Skeleton';
import { ErrorBanner } from '@/components/primitives/ErrorBanner';

export default function SystemPage() {
  const { data: services, isLoading: servicesLoading, isError } = useServices();
  const { crons, isLoading: cronsLoading } = useCrons();

  return (
    <div>
      <h1 className="text-[22px] font-[680] text-text tracking-[-0.025em] mb-6">System Health</h1>

      {isError && <ErrorBanner />}

      {/* Services */}
      <div className="mb-10">
        <div className="flex items-center gap-2 mb-4">
          <Activity className="w-3.5 h-3.5 text-text-quaternary" />
          <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">Services</span>
        </div>
        {servicesLoading ? (
          <SkeletonCards count={4} />
        ) : services ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            {Object.entries(services).map(([name, svc]) => (
              <div
                key={name}
                className="bg-bg-secondary rounded-[7px] p-4 hover:bg-bg-tertiary transition-colors"
                style={{ transitionDuration: 'var(--duration-instant)' }}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className={`w-[6px] h-[6px] rounded-full ${svc.status === 'online' ? 'bg-green' : 'bg-red'}`} />
                  <span className="text-[13px] font-[510] text-text">{name}</span>
                </div>
                <span className={`text-[10px] ${svc.status === 'online' ? 'text-green' : 'text-red'}`}>
                  {svc.detail}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </div>

      {/* Cron Jobs */}
      <div>
        <div className="flex items-center gap-2 mb-4">
          <Terminal className="w-3.5 h-3.5 text-text-quaternary" />
          <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">Cron Jobs</span>
        </div>
        {cronsLoading ? (
          <SkeletonRows count={6} />
        ) : crons.length === 0 ? (
          <p className="text-[11px] text-text-quaternary">No cron jobs</p>
        ) : (
          <div className="space-y-px">
            {crons.map((job) => (
              <div
                key={job.name}
                className="h-9 flex items-center gap-3 hover:bg-hover rounded-sm px-3 -mx-3 transition-colors"
                style={{ transitionDuration: 'var(--duration-instant)' }}
              >
                <span className={`w-[6px] h-[6px] rounded-full shrink-0 ${
                  job.status === 'ok' ? 'bg-green' :
                  job.status === 'failed' ? 'bg-red' : 'bg-text-quaternary'
                }`} />
                <span className="text-[13px] font-[510] text-text-secondary w-[140px] shrink-0">{job.name}</span>
                <span className="text-[11px] text-text-quaternary flex-1">{job.schedule}</span>
                <span className="text-[10px] font-mono text-text-quaternary">
                  {job.run_count} runs
                </span>
                {job.last_run && (
                  <span className="text-[10px] font-mono text-text-quaternary">
                    {new Date(job.last_run).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
