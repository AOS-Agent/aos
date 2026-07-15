import { useState } from 'react';
import {
  User, Bot, Cloud, Cog,
  ChevronDown, ChevronRight,
  Send, Hash, Mail, Zap, Globe,
  Shield, Eye, Sparkles,
} from 'lucide-react';
import { useOperator } from '@/hooks/useConfig';

// ═══════════════════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════════════════

type NodeType = 'human' | 'agent' | 'external-agent' | 'service';
type NodeStatus = 'active' | 'idle' | 'offline';

interface ChannelInfo { type: string; label: string; }
interface Budget { used: number; limit: number; unit: string; }

interface OrgNode {
  id: string;
  name: string;
  type: NodeType;
  role: string;
  model?: string;
  status: NodeStatus;
  capabilities?: string[];
  tools?: string[];
  trust_level?: number;
  availability?: string;
  channel?: ChannelInfo;
  budget?: Budget;
  team?: OrgNode[];
}

// ═══════════════════════════════════════════════════════════════════════════════
// Prototype Data — will come from org.yaml API
// ═══════════════════════════════════════════════════════════════════════════════

// Operator name is resolved at render time from the operator profile
// (GET /api/config/operator). Placeholder here; overridden in Org().
const OPERATOR: OrgNode = {
  id: 'operator', name: 'Operator', type: 'human', role: 'Operator', status: 'active',
};

const MIRROR: OrgNode = {
  id: 'mirror', name: 'Mirror', type: 'agent',
  role: 'Pure orchestrator — routes work, never executes',
  model: 'opus', status: 'active', tools: ['Agent'],
  capabilities: ['routing', 'delegation', 'synthesis', 'decomposition'],
};

const COUNCIL: OrgNode[] = [
  {
    id: 'advisor', name: 'Advisor', type: 'agent',
    role: 'Analysis, planning, knowledge curation',
    model: 'sonnet', status: 'idle', trust_level: 3,
    capabilities: ['analysis', 'planning', 'reviews', 'research', 'synthesis'],
    channel: { type: 'agent-tool', label: 'Subagent' },
  },
  {
    id: 'steward', name: 'Steward', type: 'agent',
    role: 'System health, risk assessment, self-correction',
    model: 'haiku', status: 'active', trust_level: 3,
    capabilities: ['monitoring', 'diagnostics', 'repair', 'drift-detection'],
    channel: { type: 'agent-tool', label: 'Subagent' },
  },
  {
    id: 'maryam', name: 'Maryam', type: 'human',
    role: 'Business strategy & market analysis',
    status: 'offline', trust_level: 3,
    capabilities: ['strategy', 'market-analysis', 'partnerships'],
    channel: { type: 'telegram', label: 'Telegram' },
    availability: 'Mon–Fri 9 AM–5 PM',
  },
];

const WORKERS: OrgNode[] = [
  {
    id: 'engineer', name: 'Engineer', type: 'agent',
    role: 'Infrastructure, services, system configuration',
    model: 'sonnet', status: 'idle', trust_level: 2,
    capabilities: ['infrastructure', 'deployment', 'services', 'launchagents', 'homebrew'],
    channel: { type: 'agent-tool', label: 'Subagent' },
    budget: { used: 12_400, limit: 50_000, unit: 'tokens' },
    team: [
      { id: 'infra-bot', name: 'Infra Bot', type: 'agent', role: 'Cloud & Terraform',
        model: 'haiku', status: 'idle', channel: { type: 'agent-tool', label: 'Subagent' },
        capabilities: ['terraform', 'cloud', 'networking'] },
      { id: 'ahmad', name: 'Ahmad', type: 'human', role: 'Networking & hardware',
        status: 'active', channel: { type: 'telegram', label: 'Telegram' },
        capabilities: ['networking', 'hardware', 'vpn'] },
      { id: 'deploy-pipeline', name: 'Deploy Pipeline', type: 'service', role: 'CI/CD automation',
        status: 'active', channel: { type: 'webhook', label: 'Webhook' },
        capabilities: ['ci-cd', 'deployment', 'rollback'] },
    ],
  },
  {
    id: 'developer', name: 'Developer', type: 'agent',
    role: 'Code changes, features, bug fixes, PRs',
    model: 'opus', status: 'active', trust_level: 1,
    capabilities: ['coding', 'testing', 'refactoring', 'pr-creation', 'debugging'],
    channel: { type: 'agent-tool', label: 'Subagent' },
    budget: { used: 31_200, limit: 100_000, unit: 'tokens' },
    team: [
      { id: 'frontend-dev', name: 'Frontend Dev', type: 'agent', role: 'React, TypeScript, UI',
        model: 'sonnet', status: 'idle', channel: { type: 'agent-tool', label: 'Subagent' },
        capabilities: ['react', 'typescript', 'css', 'components'] },
      { id: 'test-runner', name: 'Test Runner', type: 'agent', role: 'Automated testing & validation',
        model: 'haiku', status: 'idle', channel: { type: 'agent-tool', label: 'Subagent' },
        capabilities: ['testing', 'validation', 'ci'] },
    ],
  },
  {
    id: 'comms', name: 'Communications', type: 'agent',
    role: 'Messaging, channels, bridge operations',
    model: 'sonnet', status: 'idle', trust_level: 2,
    capabilities: ['telegram', 'slack', 'email', 'bridge', 'notifications'],
    channel: { type: 'agent-tool', label: 'Subagent' },
    budget: { used: 8_900, limit: 30_000, unit: 'tokens' },
    team: [
      { id: 'bridge-bot', name: 'Bridge Bot', type: 'agent', role: 'Telegram bridge management',
        model: 'haiku', status: 'active', channel: { type: 'agent-tool', label: 'Subagent' },
        capabilities: ['telegram-bridge', 'message-routing'] },
      { id: 'nora', name: 'Nora', type: 'human', role: 'Content & community',
        status: 'offline', channel: { type: 'slack', label: 'Slack' },
        capabilities: ['content', 'community', 'moderation'] },
    ],
  },
  {
    id: 'analyst', name: 'Analyst', type: 'external-agent',
    role: 'Data analysis, reporting, visualization',
    model: 'gpt-4o', status: 'idle', trust_level: 1,
    capabilities: ['data-analysis', 'sql', 'visualization', 'statistics'],
    channel: { type: 'api', label: 'OpenAI API' },
    budget: { used: 4_200, limit: 20_000, unit: 'tokens' },
  },
  {
    id: 'marketing', name: 'Marketing', type: 'agent',
    role: 'Campaigns, content, growth strategy',
    model: 'sonnet', status: 'offline', trust_level: 1,
    capabilities: ['campaigns', 'copywriting', 'seo', 'social-media'],
    channel: { type: 'agent-tool', label: 'Subagent' },
    budget: { used: 0, limit: 25_000, unit: 'tokens' },
    team: [
      { id: 'copywriter', name: 'Copywriter', type: 'agent', role: 'Marketing copy & content',
        model: 'haiku', status: 'offline', channel: { type: 'agent-tool', label: 'Subagent' },
        capabilities: ['copywriting', 'blog-posts', 'social'] },
      { id: 'sara', name: 'Sara', type: 'human', role: 'Design & visual assets',
        status: 'offline', channel: { type: 'email', label: 'Email' },
        capabilities: ['design', 'branding', 'visuals'] },
    ],
  },
];

// ═══════════════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════════════

const MODEL_COLORS: Record<string, string> = {
  opus: '#D6CCB4',
  sonnet: '#0A84FF',
  haiku: '#64D2FF',
  'gpt-4o': '#10A37F',
};

const MODEL_BG: Record<string, string> = {
  opus: 'rgba(214, 204, 180, 0.12)',
  sonnet: 'rgba(10, 132, 255, 0.12)',
  haiku: 'rgba(100, 210, 255, 0.12)',
  'gpt-4o': 'rgba(16, 163, 127, 0.12)',
};

const STATUS_COLORS: Record<NodeStatus, string> = {
  active: 'var(--color-green)',
  idle: 'var(--color-yellow)',
  offline: 'var(--color-text-quaternary)',
};

function channelIcon(type: string) {
  switch (type) {
    case 'telegram': return Send;
    case 'slack': return Hash;
    case 'email': return Mail;
    case 'webhook': return Zap;
    case 'api': return Globe;
    case 'agent-tool': return Bot;
    default: return Bot;
  }
}

function getInitials(name: string): string {
  return name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2);
}

// ═══════════════════════════════════════════════════════════════════════════════
// Avatar
// ═══════════════════════════════════════════════════════════════════════════════

function Avatar({ node, size = 40 }: { node: OrgNode; size?: number }) {
  const r = size / 2;

  if (node.type === 'human') {
    return (
      <div
        className="relative shrink-0 flex items-center justify-center rounded-full font-[600]"
        style={{
          width: size, height: size, fontSize: size * 0.36,
          background: 'linear-gradient(135deg, #B7AE97 0%, #E4DCC8 50%, #B7AE97 100%)',
          color: '#14130E',
          boxShadow: '0 0 0 2px rgba(214, 204, 180, 0.22)',
        }}
      >
        {getInitials(node.name)}
      </div>
    );
  }

  if (node.type === 'service') {
    return (
      <div
        className="relative shrink-0 flex items-center justify-center rounded-[8px]"
        style={{
          width: size, height: size,
          background: 'var(--color-bg-tertiary)',
          border: '1.5px dashed var(--color-border-tertiary)',
        }}
      >
        <Cog className="text-text-quaternary" style={{ width: size * 0.45, height: size * 0.45 }} />
      </div>
    );
  }

  if (node.type === 'external-agent') {
    const color = MODEL_COLORS[node.model ?? ''] ?? 'var(--color-text-quaternary)';
    return (
      <div
        className="relative shrink-0 flex items-center justify-center rounded-full"
        style={{
          width: size, height: size,
          background: 'var(--color-bg-tertiary)',
          border: `1.5px dashed ${color}`,
        }}
      >
        <Cloud style={{ width: size * 0.45, height: size * 0.45, color }} />
      </div>
    );
  }

  // Agent
  const color = MODEL_COLORS[node.model ?? ''] ?? 'var(--color-accent)';
  const bg = MODEL_BG[node.model ?? ''] ?? 'rgba(214, 204, 180, 0.12)';
  return (
    <div
      className="relative shrink-0 flex items-center justify-center rounded-full font-[700] uppercase"
      style={{
        width: size, height: size, fontSize: size * 0.3,
        background: bg, color,
        border: `1.5px solid ${color}`,
      }}
    >
      {(node.model ?? 'A')[0].toUpperCase()}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Status indicator
// ═══════════════════════════════════════════════════════════════════════════════

function StatusIndicator({ status, size = 8 }: { status: NodeStatus; size?: number }) {
  return (
    <span className="relative inline-flex">
      <span
        className="rounded-full"
        style={{ width: size, height: size, background: STATUS_COLORS[status] }}
      />
      {status === 'active' && (
        <span
          className="absolute inset-0 rounded-full org-pulse"
          style={{ background: STATUS_COLORS[status], opacity: 0.4 }}
        />
      )}
    </span>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Badges
// ═══════════════════════════════════════════════════════════════════════════════

function ModelBadge({ model }: { model: string }) {
  const color = MODEL_COLORS[model] ?? 'var(--color-text-quaternary)';
  const bg = MODEL_BG[model] ?? 'rgba(255,255,255,0.05)';
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-[4px] text-[10px] font-[590]"
      style={{ background: bg, color }}
    >
      {model}
    </span>
  );
}

function ChannelBadge({ channel }: { channel: ChannelInfo }) {
  const Icon = channelIcon(channel.type);
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-[4px] bg-bg-tertiary text-text-quaternary text-[10px] font-[500]">
      <Icon className="w-2.5 h-2.5" />
      {channel.label}
    </span>
  );
}

function TrustDots({ level }: { level: number }) {
  return (
    <span className="inline-flex items-center gap-0.5" title={`Trust level ${level}/3`}>
      {[0, 1, 2].map(i => (
        <span
          key={i}
          className="rounded-full"
          style={{
            width: 5, height: 5,
            background: i < level ? 'var(--color-accent)' : 'var(--color-bg-quaternary)',
          }}
        />
      ))}
    </span>
  );
}

function BudgetBar({ budget }: { budget: Budget }) {
  const pct = Math.round((budget.used / budget.limit) * 100);
  const color = pct > 80 ? 'var(--color-red)' : pct > 50 ? 'var(--color-yellow)' : 'var(--color-green)';
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-[3px] rounded-full bg-bg-quaternary overflow-hidden">
        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="text-[9px] font-mono text-text-quaternary whitespace-nowrap">
        {(budget.used / 1000).toFixed(1)}k / {(budget.limit / 1000).toFixed(0)}k
      </span>
    </div>
  );
}

function CapPills({ caps, max = 4 }: { caps: string[]; max?: number }) {
  const shown = caps.slice(0, max);
  const extra = caps.length - max;
  return (
    <div className="flex flex-wrap gap-1">
      {shown.map(c => (
        <span key={c} className="px-1.5 py-px rounded-[3px] bg-bg-tertiary text-text-quaternary text-[9px] font-[500]">
          {c}
        </span>
      ))}
      {extra > 0 && (
        <span className="px-1.5 py-px rounded-[3px] bg-bg-tertiary text-text-quaternary text-[9px] font-[500]">
          +{extra}
        </span>
      )}
    </div>
  );
}

function TypeLabel({ type }: { type: NodeType }) {
  const labels: Record<NodeType, string> = {
    human: 'Human',
    agent: 'Agent',
    'external-agent': 'External',
    service: 'Service',
  };
  const colors: Record<NodeType, string> = {
    human: 'var(--color-yellow)',
    agent: 'var(--color-blue)',
    'external-agent': 'var(--color-purple)',
    service: 'var(--color-teal)',
  };
  return (
    <span
      className="text-[9px] font-[590] uppercase tracking-[0.06em]"
      style={{ color: colors[type] }}
    >
      {labels[type]}
    </span>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Connector lines
// ═══════════════════════════════════════════════════════════════════════════════

function VConnector({ height = 32 }: { height?: number }) {
  return <div className="mx-auto" style={{ width: 1, height, background: 'var(--color-border-secondary)' }} />;
}

function HFanout() {
  return (
    <div className="flex items-center justify-center">
      <div className="mx-auto" style={{ width: 1, height: 20, background: 'var(--color-border-secondary)' }} />
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Operator node
// ═══════════════════════════════════════════════════════════════════════════════

function OperatorNode({ node }: { node: OrgNode }) {
  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative">
        <Avatar node={node} size={52} />
        <div className="absolute -bottom-0.5 -right-0.5">
          <StatusIndicator status={node.status} size={10} />
        </div>
      </div>
      <div className="text-center">
        <div className="text-[13px] font-[600] text-text">{node.name}</div>
        <div className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">
          {node.role}
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Mirror node — the orchestrator, center stage
// ═══════════════════════════════════════════════════════════════════════════════

function MirrorNode({ node }: { node: OrgNode }) {
  return (
    <div className="org-mirror-card relative flex items-center gap-4 px-5 py-4 rounded-[12px]">
      {/* Accent left edge */}
      <div
        className="absolute left-0 top-3 bottom-3 w-[3px] rounded-full"
        style={{ background: 'var(--color-accent)' }}
      />

      <div className="relative">
        <Avatar node={node} size={48} />
        <div className="absolute -bottom-0.5 -right-0.5">
          <StatusIndicator status={node.status} size={10} />
        </div>
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="text-[15px] font-[650] text-text">{node.name}</span>
          {node.model && <ModelBadge model={node.model} />}
          <Sparkles className="w-3.5 h-3.5 text-accent" />
        </div>
        <div className="text-[12px] text-text-tertiary mb-2">{node.role}</div>
        {node.tools && (
          <div className="flex items-center gap-1.5">
            <span className="text-[9px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">Tools:</span>
            {node.tools.map(t => (
              <span key={t} className="px-1.5 py-px rounded-[3px] bg-accent/10 text-accent text-[10px] font-[590]">
                {t}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Council panel — advisory body on the right
// ═══════════════════════════════════════════════════════════════════════════════

function CouncilPanel({ members }: { members: OrgNode[] }) {
  return (
    <div className="org-council-panel rounded-[12px] p-4 h-fit">
      {/* Header */}
      <div className="flex items-center gap-2 mb-4">
        <div className="w-5 h-5 rounded-full bg-purple/10 flex items-center justify-center">
          <Eye className="w-3 h-3 text-purple" />
        </div>
        <span className="text-[10px] font-[590] uppercase tracking-[0.08em] text-purple">
          Council
        </span>
        <span className="text-[10px] text-text-quaternary">
          {members.length} members
        </span>
      </div>

      <div className="text-[11px] text-text-quaternary mb-4 leading-relaxed">
        Advisory body. Mirror consults before high-stakes decisions.
        Deliberates — does not execute.
      </div>

      {/* Members */}
      <div className="space-y-3">
        {members.map(member => (
          <div
            key={member.id}
            className="flex items-start gap-3 p-3 rounded-[8px] bg-bg-secondary/50 hover:bg-bg-secondary transition-colors duration-150"
          >
            <div className="relative mt-0.5">
              <Avatar node={member} size={32} />
              <div className="absolute -bottom-0.5 -right-0.5">
                <StatusIndicator status={member.status} size={7} />
              </div>
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5 mb-0.5">
                <span className="text-[12px] font-[590] text-text">{member.name}</span>
                <TypeLabel type={member.type} />
              </div>
              <div className="text-[11px] text-text-tertiary mb-1.5">{member.role}</div>
              <div className="flex items-center gap-1.5 flex-wrap">
                {member.model && <ModelBadge model={member.model} />}
                {member.channel && <ChannelBadge channel={member.channel} />}
                {member.trust_level != null && <TrustDots level={member.trust_level} />}
              </div>
              {member.availability && (
                <div className="text-[9px] text-text-quaternary mt-1.5 font-mono">
                  {member.availability}
                </div>
              )}
              {member.capabilities && (
                <div className="mt-2">
                  <CapPills caps={member.capabilities} max={3} />
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Team member row (inside an expanded worker)
// ═══════════════════════════════════════════════════════════════════════════════

function TeamMemberRow({ node, index }: { node: OrgNode; index: number }) {
  return (
    <div
      className="flex items-center gap-3 px-3 py-2.5 rounded-[6px] bg-bg-secondary/30 hover:bg-bg-secondary/60 transition-colors duration-150 org-team-member"
      style={{ animationDelay: `${index * 60}ms` }}
    >
      <div className="relative">
        <Avatar node={node} size={26} />
        <div className="absolute -bottom-px -right-px">
          <StatusIndicator status={node.status} size={6} />
        </div>
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] font-[560] text-text">{node.name}</span>
          <TypeLabel type={node.type} />
        </div>
        <div className="text-[10px] text-text-quaternary truncate">{node.role}</div>
      </div>
      <div className="flex items-center gap-1 shrink-0">
        {node.model && <ModelBadge model={node.model} />}
        {node.channel && <ChannelBadge channel={node.channel} />}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Worker card — expandable agent with team
// ═══════════════════════════════════════════════════════════════════════════════

function WorkerCard({ node }: { node: OrgNode }) {
  const [expanded, setExpanded] = useState(false);
  const hasTeam = node.team && node.team.length > 0;
  const modelColor = MODEL_COLORS[node.model ?? ''] ?? 'var(--color-accent)';

  return (
    <div className="org-worker-card rounded-[10px] overflow-hidden">
      {/* Model color accent bar at top */}
      <div className="h-[2px]" style={{ background: modelColor }} />

      {/* Main content */}
      <div className="p-4">
        {/* Header row */}
        <div className="flex items-start gap-3 mb-3">
          <div className="relative mt-0.5">
            <Avatar node={node} size={36} />
            <div className="absolute -bottom-0.5 -right-0.5">
              <StatusIndicator status={node.status} size={8} />
            </div>
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 mb-0.5">
              <span className="text-[13px] font-[620] text-text">{node.name}</span>
              <TypeLabel type={node.type} />
            </div>
            <div className="text-[11px] text-text-tertiary leading-snug">{node.role}</div>
          </div>
        </div>

        {/* Meta row */}
        <div className="flex items-center gap-1.5 flex-wrap mb-3">
          {node.model && <ModelBadge model={node.model} />}
          {node.channel && <ChannelBadge channel={node.channel} />}
          {node.trust_level != null && <TrustDots level={node.trust_level} />}
        </div>

        {/* Capabilities */}
        {node.capabilities && (
          <div className="mb-3">
            <CapPills caps={node.capabilities} max={5} />
          </div>
        )}

        {/* Budget */}
        {node.budget && (
          <div className="mb-3">
            <BudgetBar budget={node.budget} />
          </div>
        )}

        {/* Team toggle */}
        {hasTeam && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1.5 text-[11px] font-[560] text-text-tertiary hover:text-text transition-colors duration-150 cursor-pointer w-full"
          >
            {expanded
              ? <ChevronDown className="w-3.5 h-3.5" />
              : <ChevronRight className="w-3.5 h-3.5" />
            }
            <span>Team</span>
            <span className="text-text-quaternary">({node.team!.length})</span>
            <div className="flex-1" />
            <div className="flex items-center -space-x-1.5">
              {node.team!.slice(0, 4).map(m => (
                <div key={m.id} className="w-4 h-4 rounded-full border border-bg-panel overflow-hidden">
                  <Avatar node={m} size={16} />
                </div>
              ))}
            </div>
          </button>
        )}
      </div>

      {/* Expanded team */}
      <div
        className="overflow-hidden transition-all duration-300 ease-out"
        style={{ maxHeight: expanded ? `${(node.team?.length ?? 0) * 80 + 24}px` : '0px' }}
      >
        <div className="px-3 pb-3 space-y-1.5 border-t border-border/50 pt-3">
          {node.team?.map((member, i) => (
            <TeamMemberRow key={member.id} node={member} index={i} />
          ))}
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Stats bar
// ═══════════════════════════════════════════════════════════════════════════════

function StatsBar() {
  const allNodes = [OPERATOR, MIRROR, ...COUNCIL, ...WORKERS, ...WORKERS.flatMap(w => w.team ?? [])];
  const humans = allNodes.filter(n => n.type === 'human').length;
  const agents = allNodes.filter(n => n.type === 'agent').length;
  const external = allNodes.filter(n => n.type === 'external-agent').length;
  const services = allNodes.filter(n => n.type === 'service').length;
  const active = allNodes.filter(n => n.status === 'active').length;

  const stats = [
    { label: 'Total', value: allNodes.length, color: 'var(--color-text-secondary)' },
    { label: 'Humans', value: humans, color: 'var(--color-yellow)' },
    { label: 'Agents', value: agents, color: 'var(--color-blue)' },
    { label: 'External', value: external, color: 'var(--color-purple)' },
    { label: 'Services', value: services, color: 'var(--color-teal)' },
    { label: 'Active', value: active, color: 'var(--color-green)' },
  ];

  return (
    <div className="flex items-center gap-4 px-4 py-2.5 rounded-[8px] bg-bg-panel/60 border border-border/30">
      {stats.map((s, i) => (
        <div key={s.label} className="flex items-center gap-2">
          {i > 0 && <div className="w-px h-3 bg-border/50" />}
          <span className="text-[18px] font-[700] font-mono" style={{ color: s.color }}>{s.value}</span>
          <span className="text-[10px] font-[500] text-text-quaternary uppercase tracking-[0.04em]">{s.label}</span>
        </div>
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Main page
// ═══════════════════════════════════════════════════════════════════════════════

export default function Org() {
  const { data: op } = useOperator();
  const operator: OrgNode = { ...OPERATOR, name: op?.name || 'Operator' };

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[1100px] mx-auto px-6 pt-16 pb-12">

        {/* ── Stats bar ── */}
        <div className="flex justify-center mb-10">
          <StatsBar />
        </div>

        {/* ── Command chain: Operator → Mirror ── */}
        <div className="flex flex-col items-center mb-6">
          <OperatorNode node={operator} />
          <VConnector height={28} />
          <div className="w-full max-w-[420px]">
            <MirrorNode node={MIRROR} />
          </div>
        </div>

        {/* ── Dispatch label ── */}
        <div className="flex items-center gap-3 mb-6">
          <div className="flex-1 h-px bg-border/50" />
          <div className="flex items-center gap-2">
            <Shield className="w-3 h-3 text-text-quaternary" />
            <span className="text-[9px] font-[590] uppercase tracking-[0.1em] text-text-quaternary">
              Dispatches &amp; Consults
            </span>
            <Shield className="w-3 h-3 text-text-quaternary" />
          </div>
          <div className="flex-1 h-px bg-border/50" />
        </div>

        {/* ── Main area: Workers + Council ── */}
        <div className="flex gap-6 items-start">

          {/* Workers grid */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-4">
              <span className="text-[10px] font-[590] uppercase tracking-[0.08em] text-teal">
                Execution
              </span>
              <span className="text-[10px] text-text-quaternary">
                {WORKERS.length} agents, {WORKERS.reduce((a, w) => a + (w.team?.length ?? 0), 0)} team members
              </span>
            </div>
            <div className="grid grid-cols-2 gap-4">
              {WORKERS.map(worker => (
                <WorkerCard key={worker.id} node={worker} />
              ))}
            </div>
          </div>

          {/* Council panel */}
          <div className="w-[300px] shrink-0 sticky top-16">
            <CouncilPanel members={COUNCIL} />
          </div>
        </div>
      </div>

      {/* ── Animations ── */}
      <style>{`
        .org-mirror-card {
          background: var(--glass-bg);
          border: 1px solid var(--color-accent);
          box-shadow:
            0 0 0 1px rgba(214, 204, 180, 0.1),
            0 0 20px rgba(214, 204, 180, 0.06),
            var(--glass-shadow);
          animation: org-heartbeat 3s ease-in-out infinite;
        }

        @keyframes org-heartbeat {
          0%, 100% { box-shadow: 0 0 0 1px rgba(214, 204, 180, 0.1), 0 0 20px rgba(214, 204, 180, 0.06), var(--glass-shadow); }
          50% { box-shadow: 0 0 0 2px rgba(214, 204, 180, 0.15), 0 0 30px rgba(214, 204, 180, 0.1), var(--glass-shadow); }
        }

        .org-council-panel {
          background: var(--glass-bg);
          border: 1px solid rgba(191, 90, 242, 0.15);
          box-shadow: var(--glass-shadow);
        }

        .org-worker-card {
          background: var(--color-bg-panel);
          border: 1px solid var(--color-border);
          transition: border-color var(--duration-fast) var(--ease-out),
                      box-shadow var(--duration-fast) var(--ease-out);
        }
        .org-worker-card:hover {
          border-color: var(--color-border-secondary);
          box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
        }

        .org-pulse {
          animation: org-pulse-ring 2s ease-out infinite;
        }
        @keyframes org-pulse-ring {
          0% { transform: scale(1); opacity: 0.4; }
          100% { transform: scale(2.2); opacity: 0; }
        }

        .org-team-member {
          animation: org-team-slide-in 200ms ease-out both;
        }
        @keyframes org-team-slide-in {
          from { opacity: 0; transform: translateY(-6px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}
