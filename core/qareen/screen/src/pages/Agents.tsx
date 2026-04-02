import { useState } from 'react';
import { Bot, Shield, X, Sparkles } from 'lucide-react';
import { useAgents } from '@/hooks/useAgents';
import type { AgentMeta } from '@/hooks/useAgents';
import { SkeletonCards } from '@/components/primitives/Skeleton';
import { ErrorBanner } from '@/components/primitives/ErrorBanner';
import { Tag } from '@/components/primitives/Tag';
import { EmptyState } from '@/components/primitives/EmptyState';

const SYSTEM_AGENTS = ['chief', 'steward', 'advisor'];

const TRUST_LABELS: Record<number, string> = {
  0: 'Observe', 1: 'Surface', 2: 'Draft',
  3: 'Act + Digest', 4: 'Act + Audit', 5: 'Autonomous',
};

const TRUST_COLORS = ['bg-text-quaternary', 'bg-text-tertiary', 'bg-yellow', 'bg-accent', 'bg-accent-hover', 'bg-green'];

/* ---------- Trust Bar ---------- */
function TrustBar({ level }: { level: number }) {
  return (
    <div className="flex items-center gap-1.5">
      <div className="flex gap-[3px]">
        {[0, 1, 2, 3, 4, 5].map(i => (
          <div
            key={i}
            className={`w-[14px] h-[5px] rounded-full transition-all duration-300 ${
              i <= level ? TRUST_COLORS[level] : 'bg-bg-quaternary'
            }`}
          />
        ))}
      </div>
      <span className="text-[10px] font-[510] text-text-quaternary ml-1">
        {TRUST_LABELS[level] ?? `Level ${level}`}
      </span>
    </div>
  );
}

/* ---------- Agent Card ---------- */
function AgentCard({ agent, onClick }: { agent: AgentMeta; onClick: () => void }) {
  const isSystem = agent.is_system ?? SYSTEM_AGENTS.includes(agent.name.toLowerCase());

  return (
    <button
      onClick={onClick}
      className="w-full text-left bg-bg-secondary rounded-[7px] p-6 hover:bg-bg-tertiary transition-all border border-border hover:border-border-secondary cursor-pointer group"
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      {/* Header */}
      <div className="flex items-start gap-3 mb-3">
        <div className="w-10 h-10 rounded-[7px] bg-bg-tertiary group-hover:bg-bg-quaternary transition-colors flex items-center justify-center shrink-0">
          <Bot className="w-5 h-5 text-text-quaternary group-hover:text-text-tertiary transition-colors" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="text-[16px] font-[600] text-text tracking-[-0.01em] capitalize font-serif">{agent.name}</h2>
            {isSystem && <Tag label="System" color="purple" size="sm" />}
          </div>
          <p className="text-[12px] text-text-tertiary leading-relaxed mt-1 line-clamp-2 font-serif">
            {(agent.description || '').replace(/^.*?--\s*/, '').split('.').slice(0, 2).join('.') + '.'}
          </p>
        </div>
      </div>

      {/* Skills / tags */}
      <div className="flex items-center gap-2 flex-wrap mt-4">
        <span className="text-[10px] font-[510] text-text-quaternary bg-bg-tertiary rounded-[3px] px-2 py-0.5">
          {agent.model}
        </span>
        {(agent.tools === '*' || (Array.isArray(agent.tools) && agent.tools.includes('*'))) && (
          <span className="text-[10px] font-[510] text-accent/80 bg-accent-subtle rounded-[3px] px-2 py-0.5">
            all tools
          </span>
        )}
      </div>
    </button>
  );
}

/* ---------- Agent Detail Panel ---------- */
function AgentDetailPanel({ agent, onClose }: { agent: AgentMeta; onClose: () => void }) {
  const isSystem = agent.is_system ?? SYSTEM_AGENTS.includes(agent.name.toLowerCase());

  return (
    <div className="w-full lg:w-[440px] shrink-0 border-t lg:border-t-0 lg:border-l border-border bg-bg-panel overflow-y-auto fixed inset-0 lg:static z-50 lg:z-auto">
      <div className="p-6">
        {/* Close row */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-[7px] bg-bg-secondary flex items-center justify-center">
              <Bot className="w-5 h-5 text-text-tertiary" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-[18px] font-[650] text-text tracking-[-0.02em] capitalize font-serif">{agent.name}</h2>
                {isSystem && <Tag label="System" color="purple" size="sm" />}
              </div>
            </div>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center rounded-[5px] hover:bg-hover text-text-tertiary cursor-pointer transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Description */}
        <p className="text-[13px] text-text-tertiary leading-[1.65] mb-8 font-serif">{agent.description}</p>

        {/* Metadata */}
        <div className="space-y-4 mb-8">
          <div className="flex items-center justify-between py-1">
            <span className="text-[11px] text-text-quaternary">Model</span>
            <Tag label={agent.model} color="blue" size="sm" />
          </div>
          <div className="flex items-center justify-between py-1">
            <span className="text-[11px] text-text-quaternary">Tools</span>
            <span className="text-[12px] text-text-secondary">
              {agent.tools === '*' || (Array.isArray(agent.tools) && agent.tools.includes('*'))
                ? 'All tools'
                : Array.isArray(agent.tools) ? agent.tools.join(', ') : agent.tools
              }
            </span>
          </div>
        </div>

        {/* Trust Levels */}
        <div className="flex items-center gap-2 mb-4">
          <Shield className="w-3.5 h-3.5 text-text-quaternary" />
          <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">Trust levels</span>
        </div>
        <div className="bg-bg-secondary rounded-[7px] border border-border overflow-hidden">
          {Object.entries(TRUST_LABELS).map(([level, label]) => {
            const lvl = parseInt(level);
            return (
              <div key={level} className="flex items-center justify-between px-4 py-3 border-b border-border last:border-b-0">
                <div className="flex items-center gap-3">
                  <div className={`w-2 h-2 rounded-full ${TRUST_COLORS[lvl]}`} />
                  <span className="text-[12px] text-text-secondary font-serif">{label}</span>
                </div>
                <span className="text-[10px] font-mono text-text-quaternary">Level {level}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ---------- Main Page ---------- */
export default function AgentsPage() {
  const { data: agents, isLoading, isError } = useAgents();
  const [selectedAgent, setSelectedAgent] = useState<AgentMeta | null>(null);

  return (
    <div className="flex flex-col h-full overflow-hidden bg-bg">
      {/* Page header */}
      <div className="shrink-0 px-6 md:px-10 py-6 md:py-8">
        <div className="flex items-center gap-2.5 mb-1">
          <Sparkles className="w-4 h-4 text-text-quaternary" />
          <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">Agent roster</span>
        </div>
        <p className="text-[13px] text-text-tertiary font-serif mt-1">Active agents and their trust configurations.</p>
      </div>

      {isError && <div className="px-6 md:px-10"><ErrorBanner /></div>}

      <div className="flex-1 flex min-h-0 overflow-hidden">
        <div className="flex-1 overflow-y-auto px-6 md:px-10 pb-8">
          {isLoading ? (
            <SkeletonCards count={3} />
          ) : !agents || agents.length === 0 ? (
            <EmptyState
              icon={<Bot />}
              title="No agents registered"
              description="Agent definitions will appear here once configured in ~/.claude/agents/"
            />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 max-w-[1200px]">
              {agents.map((agent) => (
                <AgentCard key={agent.id} agent={agent} onClick={() => setSelectedAgent(agent)} />
              ))}
            </div>
          )}
        </div>

        {selectedAgent && (
          <AgentDetailPanel agent={selectedAgent} onClose={() => setSelectedAgent(null)} />
        )}
      </div>
    </div>
  );
}
