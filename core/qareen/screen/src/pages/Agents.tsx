import { useState } from 'react';
import { Bot, Shield, X } from 'lucide-react';
import { useAgents } from '@/hooks/useAgents';
import type { AgentMeta } from '@/hooks/useAgents';
import { SkeletonCards } from '@/components/primitives/Skeleton';
import { ErrorBanner } from '@/components/primitives/ErrorBanner';
import { Tag } from '@/components/primitives/Tag';
import { SectionHeader } from '@/components/primitives/SectionHeader';
import { EmptyState } from '@/components/primitives/EmptyState';

const SYSTEM_AGENTS = ['chief', 'steward', 'advisor'];

const TRUST_LABELS: Record<number, string> = {
  0: 'Observe', 1: 'Surface', 2: 'Draft',
  3: 'Act + Digest', 4: 'Act + Audit', 5: 'Autonomous',
};

function AgentCard({ agent, onClick }: { agent: AgentMeta; onClick: () => void }) {
  const isSystem = agent.is_system ?? SYSTEM_AGENTS.includes(agent.name.toLowerCase());
  return (
    <button onClick={onClick} className="w-full text-left bg-bg-secondary rounded-[7px] p-5 hover:bg-bg-tertiary transition-colors border border-border" style={{ transitionDuration: 'var(--duration-instant)' }}>
      <div className="flex items-center gap-2.5 mb-1.5">
        <Bot className="w-4 h-4 text-text-quaternary" />
        <h2 className="text-[15px] font-[590] text-text tracking-[-0.01em] capitalize">{agent.name}</h2>
        {isSystem && <Tag label="System" color="purple" />}
      </div>
      <p className="text-[13px] text-text-tertiary leading-relaxed mb-4 line-clamp-2">
        {(agent.description || '').replace(/^.*?--\s*/, '').split('.').slice(0, 2).join('.') + '.'}
      </p>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] font-[510] text-text-quaternary bg-bg-tertiary rounded-[3px] px-1.5 py-0.5">{agent.model}</span>
        {(agent.tools === '*' || (Array.isArray(agent.tools) && agent.tools.includes('*'))) && <span className="text-[10px] font-[510] text-text-quaternary bg-bg-tertiary rounded-[3px] px-1.5 py-0.5">all tools</span>}
      </div>
    </button>
  );
}

function AgentDetailPanel({ agent, onClose }: { agent: AgentMeta; onClose: () => void }) {
  const isSystem = agent.is_system ?? SYSTEM_AGENTS.includes(agent.name.toLowerCase());
  return (
    <div className="w-full lg:w-[420px] shrink-0 border-t lg:border-t-0 lg:border-l border-border bg-bg-panel overflow-y-auto fixed inset-0 lg:static z-50 lg:z-auto">
      <div className="p-5">
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-2">
            <Bot className="w-5 h-5 text-text-tertiary" />
            <h2 className="text-[17px] font-[650] text-text tracking-[-0.01em] capitalize">{agent.name}</h2>
            {isSystem && <Tag label="System" color="purple" />}
          </div>
          <button onClick={onClose} className="w-8 h-8 flex items-center justify-center rounded-sm hover:bg-hover text-text-tertiary"><X className="w-4 h-4" /></button>
        </div>
        <p className="text-[13px] text-text-tertiary leading-relaxed mb-6">{agent.description}</p>
        <div className="space-y-3 mb-6">
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-text-quaternary">Model</span>
            <Tag label={agent.model} color="blue" />
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-text-quaternary">Tools</span>
            <span className="text-[12px] text-text-secondary">{agent.tools === '*' || (Array.isArray(agent.tools) && agent.tools.includes('*')) ? 'All tools' : Array.isArray(agent.tools) ? agent.tools.join(', ') : agent.tools}</span>
          </div>
        </div>
        <SectionHeader label="Trust Levels" icon={<Shield />} />
        <div className="space-y-1">
          {Object.entries(TRUST_LABELS).map(([level, label]) => (
            <div key={level} className="flex items-center justify-between py-1">
              <span className="text-[12px] text-text-tertiary">{label}</span>
              <span className="text-[10px] font-mono text-text-quaternary">Level {level}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function AgentsPage() {
  const { data: agents, isLoading, isError } = useAgents();
  const [selectedAgent, setSelectedAgent] = useState<AgentMeta | null>(null);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="shrink-0 px-5 md:px-8 py-4 md:py-6">
        <h1 className="type-title mb-2">Agents</h1>
        <p className="type-caption text-text-quaternary">Active agents and their trust configurations.</p>
      </div>
      {isError && <div className="px-5 md:px-8"><ErrorBanner /></div>}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        <div className="flex-1 overflow-y-auto px-5 md:px-8 pb-8">
          {isLoading ? <SkeletonCards count={3} /> : !agents || agents.length === 0 ? (
            <EmptyState icon={<Bot />} title="No agents found" description="Agent definitions not found. Check ~/.claude/agents/" />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {agents.map((agent) => <AgentCard key={agent.id} agent={agent} onClick={() => setSelectedAgent(agent)} />)}
            </div>
          )}
        </div>
        {selectedAgent && <AgentDetailPanel agent={selectedAgent} onClose={() => setSelectedAgent(null)} />}
      </div>
    </div>
  );
}
