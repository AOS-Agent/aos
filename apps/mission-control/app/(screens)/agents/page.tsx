'use client';

import { Bot } from 'lucide-react';
import { useAgents } from '@/hooks/useAgents';
import { SkeletonCards } from '@/components/primitives/Skeleton';
import { ErrorBanner } from '@/components/primitives/ErrorBanner';

export default function AgentsPage() {
  const { data: agents, isLoading, isError } = useAgents();

  return (
    <div>
      <h1 className="text-[22px] font-[680] text-text tracking-[-0.025em] mb-6">Agents</h1>

      {isError && <ErrorBanner />}

      {isLoading ? (
        <SkeletonCards count={3} />
      ) : !agents || agents.length === 0 ? (
        <p className="text-[13px] text-text-quaternary">No agents found in ~/.claude/agents/</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {agents.map((agent) => (
            <div
              key={agent.id}
              className="bg-bg-secondary rounded-[7px] p-5 hover:bg-bg-tertiary transition-colors"
              style={{ transitionDuration: 'var(--duration-instant)' }}
            >
              <div className="flex items-center gap-2.5 mb-1.5">
                <Bot className="w-4 h-4 text-text-quaternary" />
                <h2 className="text-[15px] font-[590] text-text tracking-[-0.011em] capitalize">
                  {agent.name}
                </h2>
              </div>
              <p className="text-[13px] text-text-tertiary leading-relaxed mb-4">
                {agent.description.replace(/^.*?--\s*/, '').split('.').slice(0, 2).join('.') + '.'}
              </p>

              <div className="flex items-center gap-2">
                <span className="text-[10px] font-[510] text-text-quaternary bg-bg-tertiary rounded-[3px] px-1.5 py-0.5">
                  {agent.model}
                </span>
                {agent.tools === '*' && (
                  <span className="text-[10px] font-[510] text-text-quaternary bg-bg-tertiary rounded-[3px] px-1.5 py-0.5">
                    all tools
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
