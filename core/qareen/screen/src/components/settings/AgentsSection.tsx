import { Bot } from 'lucide-react';
import { useAgents, type Agent } from '@/hooks/useAgents';
import { Tag } from '@/components/primitives';
import { SettingCard, SettingRow, LoadingRows } from './shared';
import type { SettingsSection } from './types';

// ---------------------------------------------------------------------------
// Agents — active agents with model, trust level, and tools.
// System agents (chief, steward, advisor) are symlinked from core/agents/.
// Catalog agents (engineer, onboard, etc.) are copied from templates/.
// ---------------------------------------------------------------------------

const SYSTEM_AGENTS = new Set(['chief', 'steward', 'advisor']);

const TRUST_LABELS: Record<number, string> = {
  0: 'Observe',
  1: 'Surface',
  2: 'Draft',
  3: 'Act + digest',
  4: 'Act + audit',
  5: 'Autonomous',
};

function toolsSummary(tools: string | string[]): string {
  if (tools === '*') return 'All tools';
  if (Array.isArray(tools)) return `${tools.length} tools`;
  return String(tools);
}

function AgentRow({ agent }: { agent: Agent }) {
  const isSystem = agent.is_system || SYSTEM_AGENTS.has(agent.name.toLowerCase());
  const trustLevel = agent.default_trust;
  const color = agent.color || undefined;

  return (
    <div className="flex items-center justify-between py-3 min-h-[44px]">
      <div className="flex items-center gap-3 min-w-0 flex-1">
        <div
          className="w-2 h-2 rounded-full shrink-0"
          style={{ backgroundColor: color || (isSystem ? '#BF5AF2' : '#0A84FF') }}
        />
        <div className="min-w-0 flex-1">
          <span className="text-[13px] font-[510] text-text-secondary block capitalize">
            {agent.name}
          </span>
          <span className="text-[11px] text-text-quaternary block truncate">
            {toolsSummary(agent.tools)}
            {trustLevel != null && ` \u00b7 ${TRUST_LABELS[trustLevel] ?? `Level ${trustLevel}`}`}
          </span>
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <Tag label={agent.model ?? 'sonnet'} color="gray" size="sm" />
        {isSystem && <Tag label="System" color="purple" size="sm" />}
      </div>
    </div>
  );
}

function AgentsContent() {
  const { data: agents, isLoading } = useAgents();

  if (isLoading) {
    return (
      <SettingCard icon={Bot} title="Agents">
        <LoadingRows count={3} />
      </SettingCard>
    );
  }

  if (!agents || agents.length === 0) {
    return (
      <SettingCard icon={Bot} title="Agents">
        <SettingRow
          label="No agents active"
          description="Agents are installed during onboarding or activated from the catalog"
        />
      </SettingCard>
    );
  }

  // Sort: system agents first, then alphabetical
  const sorted = [...agents].sort((a, b) => {
    const aSystem = SYSTEM_AGENTS.has(a.name.toLowerCase()) ? 0 : 1;
    const bSystem = SYSTEM_AGENTS.has(b.name.toLowerCase()) ? 0 : 1;
    if (aSystem !== bSystem) return aSystem - bSystem;
    return a.name.localeCompare(b.name);
  });

  return (
    <SettingCard icon={Bot} title="Agents">
      {sorted.map((agent) => (
        <AgentRow key={agent.id ?? agent.name} agent={agent} />
      ))}
    </SettingCard>
  );
}

export const agentsSection: SettingsSection = {
  id: 'agents',
  title: 'Agents',
  icon: Bot,
  component: AgentsContent,
};
