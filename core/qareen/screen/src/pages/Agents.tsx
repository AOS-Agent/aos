import { useState, useMemo, useCallback } from 'react';
import { useRegisterPageActions, type PageAction } from '@/hooks/usePageActions';
import {
  X, Shield, Wrench, Zap, Brain, Users, Plus, Search,
  Crown, Code, Megaphone, DollarSign, Briefcase,
  Sparkles, BookOpen, Lock, Globe, ExternalLink,
  Server, Palette, Scale, Target, PenTool, Database,
  Bug, GitBranch, Mail, Share2, ArrowUpRight, ChevronRight,
  Loader2, Settings,
} from 'lucide-react';
// useNavigate not needed — Configure link uses <a> for clean page transition
import type { ReactNode } from 'react';
import { useAgents, useCatalog, useActivateAgent, useInstallCommunity } from '@/hooks/useAgents';
import type { Agent } from '@/hooks/useAgents';
import { useSkills } from '@/hooks/useSkills';
import type { Skill } from '@/hooks/useSkills';

// ============================================================
// Types & Constants
// ============================================================

type Department = 'leadership' | 'engineering' | 'operations' | 'marketing' | 'finance' | 'business';
type AgentSource = 'system' | 'catalog' | 'community';

interface CommunityAgent {
  id: string;
  name: string;
  role: string;
  department: Department;
  description: string;
  vibe: string;
  model: 'opus' | 'sonnet' | 'haiku';
  color: string;
  emoji: string;
  tools: string[];
  sourceRepo: string;
  sourceFile: string;
}

const DEPT_META: Record<Department, { label: string; color: string; icon: ReactNode }> = {
  leadership:  { label: 'Leadership',  color: '#BF5AF2', icon: <Crown className="w-3.5 h-3.5" /> },
  engineering: { label: 'Engineering', color: '#F59E0B', icon: <Code className="w-3.5 h-3.5" /> },
  operations:  { label: 'Operations',  color: '#64D2FF', icon: <Server className="w-3.5 h-3.5" /> },
  marketing:   { label: 'Marketing',   color: '#FF375F', icon: <Megaphone className="w-3.5 h-3.5" /> },
  finance:     { label: 'Finance',     color: '#30D158', icon: <DollarSign className="w-3.5 h-3.5" /> },
  business:    { label: 'Business',    color: '#0A84FF', icon: <Briefcase className="w-3.5 h-3.5" /> },
};

const TRUST_LABELS: Record<number, string> = {
  0: 'Observe', 1: 'Surface', 2: 'Draft',
  3: 'Act + Digest', 4: 'Act + Audit', 5: 'Autonomous',
};

// ============================================================
// Community Agents Catalog (curated — not on disk until installed)
// ============================================================

const COMMUNITY_AGENTS: CommunityAgent[] = [
  {
    id: 'c-frontend-dev', name: 'Frontend Developer', role: 'Web & UI', department: 'engineering',
    description: 'Expert frontend developer specializing in modern web technologies, React/Vue/Angular frameworks, UI implementation, and performance optimization.',
    vibe: 'Builds responsive, accessible web apps with pixel-perfect precision.',
    model: 'sonnet', color: '#06B6D4', emoji: '🖥️',
    tools: ['Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'engineering/engineering-frontend-developer.md',
  },
  {
    id: 'c-backend-arch', name: 'Backend Architect', role: 'Systems', department: 'engineering',
    description: 'Senior backend architect specializing in scalable system design, database architecture, API development, and cloud infrastructure.',
    vibe: 'Designs the systems that hold everything up — databases, APIs, cloud, scale.',
    model: 'opus', color: '#3B82F6', emoji: '🏗️',
    tools: ['Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'engineering/engineering-backend-architect.md',
  },
  {
    id: 'c-ai-engineer', name: 'AI Engineer', role: 'ML & AI', department: 'engineering',
    description: 'Expert AI/ML engineer specializing in machine learning model development, deployment, and integration into production systems.',
    vibe: 'Turns ML models into production features that actually scale.',
    model: 'opus', color: '#3B82F6', emoji: '🤖',
    tools: ['Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'engineering/engineering-ai-engineer.md',
  },
  {
    id: 'c-software-arch', name: 'Software Architect', role: 'Architecture', department: 'engineering',
    description: 'Expert software architect specializing in system design, domain-driven design, architectural patterns, and technical decision-making.',
    vibe: 'Designs systems that survive the team that built them. Every decision has a trade-off — name it.',
    model: 'opus', color: '#6366F1', emoji: '🏛️',
    tools: ['Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'engineering/engineering-software-architect.md',
  },
  {
    id: 'c-security-eng', name: 'Security Engineer', role: 'AppSec', department: 'operations',
    description: 'Expert application security engineer specializing in threat modeling, vulnerability assessment, and secure code review.',
    vibe: 'Models threats, reviews code, hunts vulnerabilities — security that actually holds.',
    model: 'sonnet', color: '#EF4444', emoji: '🔒',
    tools: ['Read', 'Glob', 'Grep', 'Bash'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'engineering/engineering-security-engineer.md',
  },
  {
    id: 'c-sre', name: 'SRE', role: 'Reliability', department: 'operations',
    description: 'Expert site reliability engineer specializing in SLOs, error budgets, observability, chaos engineering, and toil reduction.',
    vibe: 'Reliability is a feature. Error budgets fund velocity — spend them wisely.',
    model: 'sonnet', color: '#E63946', emoji: '🛡️',
    tools: ['Read', 'Glob', 'Grep', 'Bash'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'engineering/engineering-sre.md',
  },
  {
    id: 'c-code-reviewer', name: 'Code Reviewer', role: 'Quality', department: 'engineering',
    description: 'Expert code reviewer providing constructive, actionable feedback on correctness, maintainability, security, and performance.',
    vibe: 'Reviews code like a mentor, not a gatekeeper. Every comment teaches something.',
    model: 'sonnet', color: '#A855F7', emoji: '👁️',
    tools: ['Read', 'Glob', 'Grep'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'engineering/engineering-code-reviewer.md',
  },
  {
    id: 'c-db-optimizer', name: 'Database Optimizer', role: 'Databases', department: 'engineering',
    description: 'Expert database specialist focusing on schema design, query optimization, and indexing strategies.',
    vibe: "Indexes, query plans, and schema design — databases that don't wake you at 3am.",
    model: 'sonnet', color: '#F59E0B', emoji: '🗄️',
    tools: ['Read', 'Write', 'Edit', 'Bash'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'engineering/engineering-database-optimizer.md',
  },
  {
    id: 'c-data-eng', name: 'Data Engineer', role: 'Pipelines', department: 'engineering',
    description: 'Expert data engineer specializing in building reliable data pipelines, lakehouse architectures, and scalable data infrastructure.',
    vibe: 'Builds the pipelines that turn raw data into trusted, analytics-ready assets.',
    model: 'sonnet', color: '#F97316', emoji: '🔧',
    tools: ['Read', 'Write', 'Edit', 'Bash'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'engineering/engineering-data-engineer.md',
  },
  {
    id: 'c-ux-researcher', name: 'UX Researcher', role: 'Research', department: 'business',
    description: 'Expert user experience researcher specializing in user behavior analysis, usability testing, and data-driven design insights.',
    vibe: 'Validates design decisions with real user data, not assumptions.',
    model: 'sonnet', color: '#22C55E', emoji: '🔬',
    tools: ['Read', 'Write', 'Edit', 'WebSearch'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'design/design-ux-researcher.md',
  },
  {
    id: 'c-ui-designer', name: 'UI Designer', role: 'Visual Design', department: 'business',
    description: 'Expert UI designer specializing in visual design systems, component libraries, and pixel-perfect interface creation.',
    vibe: 'Creates beautiful, consistent, accessible interfaces that feel just right.',
    model: 'opus', color: '#A855F7', emoji: '🎨',
    tools: ['Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'design/design-ui-designer.md',
  },
  {
    id: 'c-growth-hacker', name: 'Growth Hacker', role: 'Growth', department: 'marketing',
    description: 'Expert growth strategist specializing in rapid user acquisition through data-driven experimentation.',
    vibe: "Finds the growth channel nobody's exploited yet — then scales it.",
    model: 'sonnet', color: '#22C55E', emoji: '🚀',
    tools: ['Read', 'Write', 'Edit', 'WebSearch', 'WebFetch'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'marketing/marketing-growth-hacker.md',
  },
  {
    id: 'c-citation-strat', name: 'AI Citation Strategist', role: 'AEO/GEO', department: 'marketing',
    description: 'Expert in AI recommendation engine optimization — audits brand visibility across ChatGPT, Claude, Gemini, and Perplexity.',
    vibe: 'Figures out why the AI recommends your competitor and rewires the signals.',
    model: 'sonnet', color: '#6D28D9', emoji: '🔮',
    tools: ['Read', 'Write', 'Edit', 'WebSearch', 'WebFetch'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'marketing/marketing-ai-citation-strategist.md',
  },
  {
    id: 'c-product-mgr', name: 'Product Manager', role: 'Product', department: 'business',
    description: 'Holistic product leader who owns the full product lifecycle — discovery, strategy, roadmap, go-to-market.',
    vibe: 'Ships the right thing, not just the next thing — outcome-obsessed and user-grounded.',
    model: 'opus', color: '#3B82F6', emoji: '🧭',
    tools: ['Read', 'Write', 'Edit', 'WebSearch', 'WebFetch'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'product/product-manager.md',
  },
  {
    id: 'c-feedback-synth', name: 'Feedback Synthesizer', role: 'Insights', department: 'business',
    description: 'Expert in collecting, analyzing, and synthesizing user feedback from multiple channels.',
    vibe: 'Distills a thousand user voices into the five things you need to build next.',
    model: 'sonnet', color: '#3B82F6', emoji: '🔍',
    tools: ['Read', 'Write', 'Edit', 'WebSearch', 'WebFetch'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'product/product-feedback-synthesizer.md',
  },
  {
    id: 'c-orchestrator', name: 'Agents Orchestrator', role: 'Pipeline', department: 'leadership',
    description: 'Autonomous pipeline manager that orchestrates the entire development workflow from spec to ship.',
    vibe: 'The conductor who runs the entire dev pipeline from spec to ship.',
    model: 'opus', color: '#06B6D4', emoji: '🎛️',
    tools: ['Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'Agent'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'specialized/agents-orchestrator.md',
  },
  {
    id: 'c-a11y-auditor', name: 'Accessibility Auditor', role: 'A11y', department: 'engineering',
    description: 'Expert accessibility specialist who audits interfaces against WCAG standards and tests with assistive technologies.',
    vibe: "If it's not tested with a screen reader, it's not accessible.",
    model: 'haiku', color: '#0077B6', emoji: '♿',
    tools: ['Read', 'Glob', 'Grep'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'testing/testing-accessibility-auditor.md',
  },
  {
    id: 'c-api-tester', name: 'API Tester', role: 'QA', department: 'engineering',
    description: 'Expert API testing specialist focused on comprehensive API validation, performance testing, and quality assurance.',
    vibe: 'Breaks your API before your users do.',
    model: 'haiku', color: '#A855F7', emoji: '🔌',
    tools: ['Read', 'Write', 'Bash'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'testing/testing-api-tester.md',
  },
  {
    id: 'c-deal-strat', name: 'Deal Strategist', role: 'Sales', department: 'business',
    description: 'Senior deal strategist specializing in MEDDPICC qualification, competitive positioning, and win planning.',
    vibe: 'Qualifies deals like a surgeon and kills happy ears on contact.',
    model: 'sonnet', color: '#1B4D3E', emoji: '♟️',
    tools: ['Read', 'Write', 'Edit', 'WebSearch'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'sales/sales-deal-strategist.md',
  },
  {
    id: 'c-exec-summary', name: 'Executive Summary', role: 'Strategy', department: 'leadership',
    description: 'Consultant-grade AI specialist that transforms complex business inputs into concise, actionable executive summaries.',
    vibe: 'Thinks like a McKinsey consultant, writes for the C-suite.',
    model: 'opus', color: '#A855F7', emoji: '📝',
    tools: ['Read', 'Write', 'Edit', 'WebSearch'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'support/support-executive-summary-generator.md',
  },
  {
    id: 'c-compliance', name: 'Compliance Auditor', role: 'Compliance', department: 'finance',
    description: 'Expert technical compliance auditor specializing in SOC 2, ISO 27001, HIPAA, and PCI-DSS audits.',
    vibe: 'Walks you from readiness assessment through evidence collection to certification.',
    model: 'sonnet', color: '#F97316', emoji: '📋',
    tools: ['Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'specialized/compliance-auditor.md',
  },
  {
    id: 'c-game-designer', name: 'Game Designer', role: 'Game Dev', department: 'engineering',
    description: 'Systems and mechanics architect — masters GDD authorship, player psychology, economy balancing, and gameplay loop design.',
    vibe: 'Thinks in loops, levers, and player motivations to architect compelling gameplay.',
    model: 'opus', color: '#EAB308', emoji: '🎮',
    tools: ['Read', 'Write', 'Edit', 'Bash'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'game-development/game-designer.md',
  },
  {
    id: 'c-project-shepherd', name: 'Project Shepherd', role: 'PM', department: 'operations',
    description: 'Expert project manager specializing in cross-functional coordination, timeline management, and stakeholder alignment.',
    vibe: 'Herds cross-functional chaos into on-time, on-scope delivery.',
    model: 'sonnet', color: '#3B82F6', emoji: '🐑',
    tools: ['Read', 'Write', 'Edit', 'Bash'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'project-management/project-management-project-shepherd.md',
  },
  {
    id: 'c-brand-guardian', name: 'Brand Guardian', role: 'Brand', department: 'marketing',
    description: 'Expert brand strategist and guardian specializing in brand identity development and consistency maintenance.',
    vibe: "Your brand's fiercest protector and most passionate advocate.",
    model: 'sonnet', color: '#3B82F6', emoji: '🎨',
    tools: ['Read', 'Write', 'Edit', 'WebSearch'],
    sourceRepo: 'msitarzewski/agency-agents', sourceFile: 'design/design-brand-guardian.md',
  },
];

// Category icons for skills
const CATEGORY_ICONS: Record<string, ReactNode> = {
  core: <Brain className="w-4 h-4" />,
  workflow: <GitBranch className="w-4 h-4" />,
  domain: <Sparkles className="w-4 h-4" />,
  integration: <Zap className="w-4 h-4" />,
};

// ============================================================
// Glass style
// ============================================================

const GLASS: React.CSSProperties = {
  background: 'rgba(30, 26, 22, 0.60)',
  backdropFilter: 'blur(12px)',
  WebkitBackdropFilter: 'blur(12px)',
  borderColor: 'rgba(255, 245, 235, 0.06)',
  boxShadow: '0 2px 12px rgba(0,0,0,0.3)',
};

const LINE_COLOR = 'rgba(255, 245, 235, 0.06)';

// ============================================================
// Helper: infer department from agent data
// ============================================================

function inferDepartment(agent: Agent): Department {
  const id = agent.id;
  const role = (agent.role || agent.domain || '').toLowerCase();
  if (['chief', 'advisor'].includes(id)) return 'leadership';
  if (['steward', 'ops', 'technician'].includes(id)) return 'operations';
  if (['engineer', 'developer'].includes(id)) return 'engineering';
  if (['cmo'].includes(id)) return 'marketing';
  if (role.includes('market') || role.includes('content') || role.includes('social')) return 'marketing';
  if (role.includes('financ') || role.includes('account') || role.includes('legal')) return 'finance';
  if (role.includes('engineer') || role.includes('develop') || role.includes('design')) return 'engineering';
  if (role.includes('sales') || role.includes('product') || role.includes('business')) return 'business';
  if (role.includes('ops') || role.includes('health') || role.includes('reliab')) return 'operations';
  if (agent.source === 'community') return 'engineering';
  return 'engineering';
}

// ============================================================
// Tree builder
// ============================================================

interface TreeNode {
  agent: Agent;
  children: TreeNode[];
}

function buildTree(agents: Agent[]): TreeNode[] {
  const byId = new Map<string, Agent>();
  for (const a of agents) byId.set(a.id, a);

  const childMap = new Map<string, TreeNode[]>();
  const roots: TreeNode[] = [];

  for (const a of agents) {
    const node: TreeNode = { agent: a, children: [] };
    const parentId = a.reports_to;
    if (!parentId || !byId.has(parentId)) {
      roots.push(node);
    } else {
      if (!childMap.has(parentId)) childMap.set(parentId, []);
      childMap.get(parentId)!.push(node);
    }
  }

  // Attach children recursively
  function attach(node: TreeNode) {
    const kids = childMap.get(node.agent.id) || [];
    node.children = kids;
    for (const k of kids) attach(k);
  }
  for (const r of roots) attach(r);

  return roots;
}

// ============================================================
// Small components
// ============================================================

function StatusDot({ source }: { source: string }) {
  const isSystem = source === 'system';
  return (
    <span className={`w-1.5 h-1.5 rounded-full ${isSystem ? 'bg-green animate-pulse' : 'bg-text-quaternary/40'}`} />
  );
}

function AgentAvatar({ agent, size = 'md' }: { agent: Agent; size?: 'sm' | 'md' | 'lg' }) {
  const dim = size === 'lg' ? 'w-14 h-14 text-[18px] rounded-xl' : size === 'md' ? 'w-9 h-9 text-[12px] rounded-lg' : 'w-6 h-6 text-[9px] rounded';
  return (
    <div
      className={`${dim} flex items-center justify-center shrink-0 font-[600]`}
      style={{ backgroundColor: (agent.color || '#6B6560') + '15', color: agent.color || '#6B6560' }}
    >
      {agent.initials || agent.name.slice(0, 2).toUpperCase()}
    </div>
  );
}

// ============================================================
// Hierarchy tree visualization (Roster)
// ============================================================

/* ── Hierarchy row — renders an agent with indent for depth ── */
function HierarchyRow({ agent, depth, onClick }: { agent: Agent; depth: number; onClick: () => void }) {
  const isRoot = depth === 0;
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-3 rounded-lg hover:bg-[rgba(255,245,235,0.03)] transition-colors cursor-pointer group text-left ${isRoot ? 'px-3 py-2.5' : 'px-3 py-2'}`}
      style={{ paddingLeft: `${12 + depth * 24}px` }}
    >
      {/* Indent guide line */}
      {depth > 0 && (
        <div
          className="absolute rounded-full"
          style={{
            left: `${4 + depth * 24}px`,
            width: '2px',
            top: '6px',
            bottom: '6px',
            backgroundColor: agent.color ? agent.color + '20' : LINE_COLOR,
          }}
        />
      )}

      <AgentAvatar agent={agent} size="md" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className={`font-[560] text-text tracking-[-0.01em] ${isRoot ? 'text-[14px]' : 'text-[13px]'}`}>
            {agent.name}
          </span>
          {agent.is_system && <span className="text-[9px] font-[510] text-purple/60 uppercase tracking-wider">sys</span>}
          {agent.source === 'community' && (
            <span className="text-[9px] font-[510] text-teal/60 uppercase tracking-wider">community</span>
          )}
        </div>
        <p className="text-[11px] text-text-quaternary leading-[1.4] truncate">
          {agent.role || agent.description.slice(0, 80)}
        </p>
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <span className="text-[10px] text-text-quaternary font-mono">{agent.model}</span>
        <StatusDot source={agent.source} />
        <ChevronRight className="w-3.5 h-3.5 text-text-quaternary/40 group-hover:text-text-quaternary transition-colors" />
      </div>
    </button>
  );
}

/* ── Recursive tree renderer ── */
function TreeRows({ nodes, depth, onClick }: { nodes: TreeNode[]; depth: number; onClick: (a: Agent) => void }) {
  return (
    <>
      {nodes.map(node => (
        <div key={node.agent.id} className="relative">
          <HierarchyRow agent={node.agent} depth={depth} onClick={() => onClick(node.agent)} />
          {node.children.length > 0 && (
            <TreeRows nodes={node.children} depth={depth + 1} onClick={onClick} />
          )}
        </div>
      ))}
    </>
  );
}

function HierarchyView({ agents, onSelect }: { agents: Agent[]; onSelect: (a: Agent) => void }) {
  const trees = useMemo(() => buildTree(agents), [agents]);

  if (trees.length === 0) {
    return (
      <div className="flex flex-col items-center py-20">
        <Users className="w-8 h-8 text-text-quaternary/20 mb-3" />
        <p className="text-[13px] text-text-tertiary">No agents installed</p>
      </div>
    );
  }

  return (
    <div className="py-2">
      <TreeRows nodes={trees} depth={0} onClick={onSelect} />
    </div>
  );
}

// ============================================================
// Recruit: Hire Card
// ============================================================

type RecruitAgent = Agent | CommunityAgent;

function isRealAgent(a: RecruitAgent): a is Agent {
  return 'is_system' in a;
}

function HireCard({ agent, onHire, onPreview, isInstalled, isInstalling }: {
  agent: RecruitAgent;
  onHire: () => void;
  onPreview: () => void;
  isInstalled: boolean;
  isInstalling: boolean;
}) {
  const isCommunity = !isRealAgent(agent) || agent.source === 'community';
  const name = agent.name;
  const role = agent.role;
  const model = agent.model;
  const color = agent.color || '#6B6560';
  const vibe = isCommunity && !isRealAgent(agent) ? (agent as CommunityAgent).vibe : agent.description;
  const emoji = isCommunity && !isRealAgent(agent) ? (agent as CommunityAgent).emoji : undefined;
  const sourceRepo = !isRealAgent(agent) ? (agent as CommunityAgent).sourceRepo : undefined;
  const tools = agent.tools;
  const skills = isRealAgent(agent) ? agent.skills : [];

  return (
    <div
      className="rounded-lg border border-border/50 hover:border-border-secondary transition-all cursor-pointer group overflow-hidden"
      style={{ transitionDuration: '80ms' }}
      onClick={onPreview}
    >
      <div className="h-px" style={{ backgroundColor: color + '40' }} />
      <div className="p-4">
        <div className="flex items-center gap-3 mb-2">
          {emoji ? (
            <div className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0 text-[18px]"
              style={{ backgroundColor: color + '12' }}>{emoji}</div>
          ) : (
            <div className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0 text-[12px] font-[600]"
              style={{ backgroundColor: color + '15', color }}>{name.slice(0, 2).toUpperCase()}</div>
          )}
          <div className="flex-1 min-w-0">
            <span className="text-[13px] font-[560] text-text tracking-[-0.01em] block">{name}</span>
            <span className="text-[11px] text-text-quaternary">{role}</span>
          </div>
          <span className="text-[10px] text-text-quaternary font-mono">{model}</span>
        </div>

        <div className="flex items-center gap-2 mb-2.5">
          {isCommunity ? (
            <span className="inline-flex items-center gap-1 text-[9px] font-[510] text-teal/80 bg-teal/8 px-1.5 py-0.5 rounded-[3px]">
              <ExternalLink className="w-2.5 h-2.5" /> Community
            </span>
          ) : (
            <span className="text-[9px] font-[510] text-accent/60 bg-accent/8 px-1.5 py-0.5 rounded-[3px]">AOS Catalog</span>
          )}
          {sourceRepo && <span className="text-[9px] text-text-quaternary/50 font-mono truncate">{sourceRepo}</span>}
        </div>

        <p className="text-[12px] text-text-tertiary leading-[1.5] mb-4 font-serif italic">{vibe}</p>

        <div className="flex items-center gap-3 text-[10px] text-text-quaternary mb-4">
          {tools.length > 0 && <span>{tools.length} tools</span>}
          {skills.length > 0 && <span>{skills.length} skills</span>}
        </div>

        {isInstalled ? (
          <div className="w-full h-8 rounded-md bg-green/10 text-[12px] font-[510] text-green flex items-center justify-center gap-1.5">Hired</div>
        ) : (
          <button
            onClick={e => { e.stopPropagation(); onHire(); }}
            disabled={isInstalling}
            className="w-full h-8 rounded-md bg-accent/10 hover:bg-accent/20 text-[12px] font-[510] text-accent cursor-pointer transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
            style={{ transitionDuration: '80ms' }}
          >
            {isInstalling ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <><Plus className="w-3.5 h-3.5" /> Hire</>}
          </button>
        )}
      </div>
    </div>
  );
}

// ============================================================
// Skill row
// ============================================================

function SkillRow({ skill }: { skill: Skill }) {
  const catColors: Record<string, string> = { core: 'text-accent', domain: 'text-blue', workflow: 'text-purple', integration: 'text-teal' };
  const catColor = catColors[skill.category] ?? 'text-text-quaternary';
  const icon = CATEGORY_ICONS[skill.category] ?? <Sparkles className="w-4 h-4" />;
  return (
    <div className="flex items-center gap-4 px-3 py-3 rounded-lg hover:bg-[rgba(255,245,235,0.03)] transition-colors cursor-pointer"
      style={{ transitionDuration: '80ms' }}>
      <span className="text-text-quaternary shrink-0">{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-[560] text-text">{skill.name}</span>
          <span className={`text-[10px] font-[510] ${catColor}`}>{skill.category}</span>
        </div>
        <p className="text-[11px] text-text-quaternary leading-[1.4] mt-0.5 line-clamp-2">{skill.description}</p>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {skill.triggers.slice(0, 2).map(t => (
          <span key={t} className="text-[10px] text-text-quaternary/60 font-mono hidden sm:block truncate max-w-[120px]">{t}</span>
        ))}
      </div>
    </div>
  );
}

// ============================================================
// Detail panel
// ============================================================

function Section({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <div className="mb-6">
      <div className="flex items-center gap-2 mb-2.5">
        <span className="text-text-quaternary">{icon}</span>
        <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">{title}</span>
      </div>
      {children}
    </div>
  );
}

function MiniAgent({ agent }: { agent: Agent }) {
  return (
    <div className="flex items-center gap-2.5 py-1.5 px-2 rounded-md bg-[rgba(255,245,235,0.03)]">
      <AgentAvatar agent={agent} size="sm" />
      <div>
        <span className="text-[12px] font-[510] text-text block leading-tight">{agent.name}</span>
        <span className="text-[10px] text-text-quaternary">{agent.role}</span>
      </div>
    </div>
  );
}

function AgentDetail({ agent, allAgents, onClose }: { agent: Agent; allAgents: Agent[]; onClose: () => void }) {
  const dept = DEPT_META[inferDepartment(agent)];
  const reportsToAgent = agent.reports_to ? allAgents.find(a => a.id === agent.reports_to) : null;
  const directReports = allAgents.filter(a => a.reports_to === agent.id);
  const trustLevel = agent.default_trust ?? 1;

  return (
    <div className="fixed inset-0 z-50" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
      <div
        className="absolute right-0 top-0 bottom-0 w-full max-w-[460px] bg-bg-panel overflow-y-auto animate-[slideIn_180ms_ease-out]"
        onClick={e => e.stopPropagation()}
      >
        <div className="p-6 sm:p-8">
          <button onClick={onClose} className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-hover text-text-quaternary cursor-pointer mb-8">
            <X className="w-4 h-4" />
          </button>

          {/* Identity */}
          <div className="flex items-start gap-4 mb-6">
            <AgentAvatar agent={agent} size="lg" />
            <div>
              <h2 className="text-[20px] font-[650] text-text tracking-[-0.02em] mb-1">{agent.name}</h2>
              <p className="text-[13px] text-text-tertiary">{agent.role}</p>
              <div className="flex items-center gap-3 mt-2">
                <StatusDot source={agent.source} />
                <span className="text-[11px] text-text-quaternary capitalize">{agent.source}</span>
                <span className="text-[11px] text-text-quaternary">·</span>
                <span className="text-[11px]" style={{ color: dept.color + 'B0' }}>{dept.label}</span>
                {agent.is_system && (
                  <><span className="text-[11px] text-text-quaternary">·</span><span className="text-[10px] text-purple/60 font-[510] uppercase tracking-wider">System</span></>
                )}
              </div>
            </div>
          </div>

          <p className="text-[13px] text-text-secondary leading-[1.65] mb-5">{agent.description}</p>

          <div className="mb-8">
            <a
              href={`/agents/${agent.id}`}
              className="inline-flex items-center gap-1.5 h-8 px-3 rounded-md bg-accent/10 hover:bg-accent/20 text-[12px] font-[510] text-accent transition-colors cursor-pointer"
            >
              <Settings className="w-3.5 h-3.5" /> Configure
            </a>
          </div>

          <div className="flex items-center gap-4 mb-8 text-[12px] text-text-quaternary">
            <span className="font-mono">{agent.model}</span>
            <span>·</span>
            <span>{agent.scope === 'global' ? 'Global' : 'Project'}</span>
            <span>·</span>
            <span>{TRUST_LABELS[trustLevel] ?? 'Surface'}</span>
          </div>

          <Section title="Tools" icon={<Wrench className="w-3.5 h-3.5" />}>
            {agent.tools.includes('*') ? (
              <p className="text-[12px] text-accent/80">All tools</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {agent.tools.map(t => <span key={t} className="text-[11px] font-mono text-text-tertiary bg-[rgba(255,245,235,0.04)] rounded px-2 py-0.5">{t}</span>)}
              </div>
            )}
          </Section>

          {agent.skills.length > 0 && (
            <Section title="Skills" icon={<Zap className="w-3.5 h-3.5" />}>
              <div className="flex flex-wrap gap-1.5">
                {agent.skills.map(s => <span key={s} className="text-[11px] text-text-secondary bg-[rgba(255,245,235,0.04)] rounded px-2 py-0.5">{s}</span>)}
              </div>
            </Section>
          )}

          {agent.mcp_servers.length > 0 && (
            <Section title="Integrations" icon={<Globe className="w-3.5 h-3.5" />}>
              <div className="flex flex-wrap gap-1.5">
                {agent.mcp_servers.map(s => <span key={s} className="text-[11px] text-text-secondary bg-[rgba(255,245,235,0.04)] rounded px-2 py-0.5 capitalize">{s}</span>)}
              </div>
            </Section>
          )}

          <Section title="Trust" icon={<Shield className="w-3.5 h-3.5" />}>
            <div className="flex gap-[3px] mb-2">
              {[0, 1, 2, 3, 4, 5].map(i => (
                <div key={i} className={`flex-1 h-1 rounded-full ${i <= trustLevel ? 'bg-accent' : 'bg-[rgba(255,245,235,0.06)]'}`} />
              ))}
            </div>
            <p className="text-[11px] text-text-quaternary">Level {trustLevel} — {TRUST_LABELS[trustLevel] ?? 'Surface'}</p>
          </Section>

          {(reportsToAgent || directReports.length > 0) && (
            <Section title="Hierarchy" icon={<Users className="w-3.5 h-3.5" />}>
              {reportsToAgent && (
                <div className="mb-3">
                  <span className="text-[10px] text-text-quaternary block mb-1.5">Reports to</span>
                  <MiniAgent agent={reportsToAgent} />
                </div>
              )}
              {directReports.length > 0 && (
                <div>
                  <span className="text-[10px] text-text-quaternary block mb-1.5">Direct reports</span>
                  <div className="space-y-1">{directReports.map(r => <MiniAgent key={r.id} agent={r} />)}</div>
                </div>
              )}
            </Section>
          )}

          <Section title="Permissions" icon={<Lock className="w-3.5 h-3.5" />}>
            <div className="flex items-center gap-4 text-[12px]">
              <span className="text-text-quaternary">Scope</span>
              <span className="text-text-tertiary font-mono text-[11px]">{agent.scope}</span>
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}

// ============================================================
// Main Page
// ============================================================

type View = 'roster' | 'recruit' | 'skills';
type SourceFilter = 'all' | 'catalog' | 'community';

const VIEW_LABELS: Record<View, string> = { roster: 'My Team', recruit: 'Recruit', skills: 'Skills' };

export default function AgentsPage() {
  const [view, setView] = useState<View>('roster');
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [deptFilter, setDeptFilter] = useState<Department | 'all'>('all');
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all');

  // Real data
  const { data: agents = [], isLoading } = useAgents();
  const { data: catalogAgents = [] } = useCatalog();
  const { data: skills = [], isLoading: skillsLoading } = useSkills();
  const activateMut = useActivateAgent();
  const installMut = useInstallCommunity();

  // Installed agent IDs (for marking community agents as "hired")
  const installedIds = useMemo(() => new Set(agents.map(a => a.id)), [agents]);

  // Recruit: merge catalog agents + community agents, excluding already installed
  const recruitAgents = useMemo(() => {
    const all: RecruitAgent[] = [];

    // Real catalog agents from API
    for (const ca of catalogAgents) {
      if (!installedIds.has(ca.id)) all.push(ca);
    }

    // Community agents (curated list, minus installed)
    for (const ca of COMMUNITY_AGENTS) {
      if (!installedIds.has(ca.id)) all.push(ca as unknown as RecruitAgent);
    }

    // Apply filters
    let filtered = all;
    if (sourceFilter === 'catalog') filtered = filtered.filter(a => isRealAgent(a));
    if (sourceFilter === 'community') filtered = filtered.filter(a => !isRealAgent(a) || a.source === 'community');
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      filtered = filtered.filter(a =>
        a.name.toLowerCase().includes(q) || a.role.toLowerCase().includes(q) ||
        a.description.toLowerCase().includes(q)
      );
    }
    if (deptFilter !== 'all') {
      filtered = filtered.filter(a => {
        if (isRealAgent(a)) return inferDepartment(a) === deptFilter;
        return (a as CommunityAgent).department === deptFilter;
      });
    }
    return filtered;
  }, [catalogAgents, installedIds, sourceFilter, searchQuery, deptFilter]);

  const catalogCount = catalogAgents.filter(a => !installedIds.has(a.id)).length;
  const communityCount = COMMUNITY_AGENTS.filter(a => !installedIds.has(a.id)).length;

  const pageActions: PageAction[] = useMemo(() => [
    {
      id: 'agents.switch_view',
      label: 'Switch Agents view',
      category: 'navigate',
      params: [{ name: 'view', type: 'enum' as const, required: true, description: 'View to show', options: ['roster', 'recruit', 'skills'] }],
      execute: ({ view: v }) => setView(v as View),
    },
    {
      id: 'agents.search',
      label: 'Search agents',
      category: 'search',
      params: [{ name: 'query', type: 'string' as const, required: true, description: 'Search query' }],
      execute: ({ query }) => { setView('recruit'); setSearchQuery(query as string); },
    },
    {
      id: 'agents.filter_department',
      label: 'Filter by department',
      category: 'filter',
      params: [{ name: 'dept', type: 'enum' as const, required: true, description: 'Department', options: ['all', 'leadership', 'engineering', 'operations', 'marketing', 'finance', 'business'] }],
      execute: ({ dept }) => setDeptFilter(dept as Department | 'all'),
    },
  ], [])
  useRegisterPageActions(pageActions)

  const handleHire = useCallback(async (agent: RecruitAgent) => {
    if (!isRealAgent(agent) && 'sourceRepo' in agent) {
      // Community agent
      const ca = agent as CommunityAgent;
      installMut.mutate({ repo: ca.sourceRepo, file: ca.sourceFile, id: ca.id });
    } else {
      // Catalog agent
      activateMut.mutate(agent.id);
    }
  }, [activateMut, installMut]);

  const filteredSkills = useMemo(() => {
    if (!searchQuery) return skills;
    const q = searchQuery.toLowerCase();
    return skills.filter(s =>
      s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q) ||
      s.triggers.some(t => t.toLowerCase().includes(q))
    );
  }, [searchQuery, skills]);

  return (
    <div className="h-full flex flex-col bg-bg">
      {/* Glass pill tabs */}
      <div className="shrink-0 flex justify-center pt-3 pb-2 pointer-events-none">
        <div className="flex items-center gap-1 h-8 px-1 rounded-full border pointer-events-auto" style={GLASS}>
          {(['roster', 'recruit', 'skills'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => { setView(tab); setSearchQuery(''); setDeptFilter('all'); setSourceFilter('all'); }}
              className={`px-3.5 h-6 rounded-full text-[12px] font-[510] cursor-pointer transition-all duration-150 ${
                view === tab ? 'bg-[rgba(255,245,235,0.10)] text-text' : 'text-text-tertiary hover:text-text-secondary'
              }`}
            >
              {VIEW_LABELS[tab]}
            </button>
          ))}
        </div>
      </div>

      {/* Search + filters (recruit & skills) */}
      {(view === 'recruit' || view === 'skills') && (
        <div className="shrink-0 px-6 sm:px-10 pb-3 pt-1">
          <div className="max-w-[920px] mx-auto space-y-2">
            <div className="flex items-center gap-3">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-quaternary" />
                <input
                  type="text"
                  placeholder={view === 'recruit' ? 'Search agents...' : 'Search skills...'}
                  value={searchQuery}
                  onChange={e => setSearchQuery(e.target.value)}
                  className="w-full h-8 pl-8 pr-3 rounded-full bg-transparent border border-border text-[12px] text-text placeholder:text-text-quaternary outline-none focus:border-border-secondary transition-colors"
                />
              </div>
            </div>
            {view === 'recruit' && (
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-0.5 border-r border-border/40 pr-3">
                  {([['all', `All (${catalogCount + communityCount})`], ['catalog', `AOS (${catalogCount})`], ['community', `Community (${communityCount})`]] as const).map(([key, label]) => (
                    <button
                      key={key}
                      onClick={() => setSourceFilter(key as SourceFilter)}
                      className={`h-6 px-2.5 rounded-full text-[11px] font-[510] cursor-pointer transition-colors duration-100 ${
                        sourceFilter === key ? 'bg-[rgba(255,245,235,0.08)] text-text' : 'text-text-quaternary hover:text-text-tertiary'
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-0.5 overflow-x-auto">
                  {(['all' as const, 'leadership', 'engineering', 'operations', 'marketing', 'finance', 'business'] as const).map(d => (
                    <button
                      key={d}
                      onClick={() => setDeptFilter(d)}
                      className={`h-6 px-2.5 rounded-full text-[11px] font-[510] cursor-pointer transition-colors duration-100 whitespace-nowrap ${
                        deptFilter === d ? 'bg-[rgba(255,245,235,0.08)] text-text' : 'text-text-quaternary hover:text-text-tertiary'
                      }`}
                    >
                      {d === 'all' ? 'All' : DEPT_META[d].label}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-4 sm:px-8 pb-8">
        {view === 'roster' && (
          <div className="max-w-[680px] mx-auto">
            {isLoading ? (
              <div className="flex items-center justify-center py-20">
                <Loader2 className="w-5 h-5 text-text-quaternary animate-spin" />
              </div>
            ) : (
              <HierarchyView agents={agents} onSelect={setSelectedAgent} />
            )}
          </div>
        )}

        {view === 'recruit' && (
          <div className="max-w-[920px] mx-auto">
            {recruitAgents.length === 0 ? (
              <div className="flex flex-col items-center py-20">
                <Sparkles className="w-8 h-8 text-text-quaternary/20 mb-3" />
                <p className="text-[13px] text-text-tertiary">No agents match your search</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {recruitAgents.map(agent => {
                  const id = isRealAgent(agent) ? agent.id : (agent as CommunityAgent).id;
                  const isInstalling = activateMut.isPending || installMut.isPending;
                  return (
                    <HireCard
                      key={id}
                      agent={agent}
                      onHire={() => handleHire(agent)}
                      onPreview={() => {
                        if (isRealAgent(agent)) {
                          setSelectedAgent(agent);
                        } else {
                          // Convert community agent to Agent shape for detail panel
                          const ca = agent as CommunityAgent;
                          setSelectedAgent({
                            id: ca.id, name: ca.name, role: ca.role, domain: '',
                            description: ca.vibe || ca.description, model: ca.model,
                            color: ca.color, initials: ca.emoji || ca.name.slice(0, 2).toUpperCase(),
                            tools: ca.tools, skills: [], mcp_servers: [],
                            scope: 'project', reports_to: null, source: 'community',
                            default_trust: 2, is_system: false, is_active: false, schedule: {},
                          });
                        }
                      }}
                      isInstalled={installedIds.has(id)}
                      isInstalling={isInstalling}
                    />
                  );
                })}
              </div>
            )}
          </div>
        )}

        {view === 'skills' && (
          <div className="max-w-[680px] mx-auto">
            {skillsLoading ? (
              <div className="flex items-center justify-center py-20">
                <Loader2 className="w-5 h-5 text-text-quaternary animate-spin" />
              </div>
            ) : filteredSkills.length === 0 ? (
              <div className="flex flex-col items-center py-20">
                <BookOpen className="w-8 h-8 text-text-quaternary/20 mb-3" />
                <p className="text-[13px] text-text-tertiary">No skills match your search</p>
              </div>
            ) : (
              filteredSkills.map(skill => <SkillRow key={skill.id} skill={skill} />)
            )}
          </div>
        )}
      </div>

      {selectedAgent && (
        <AgentDetail agent={selectedAgent} allAgents={agents} onClose={() => setSelectedAgent(null)} />
      )}

      <style>{`
        @keyframes slideIn {
          from { transform: translateX(100%); }
          to { transform: translateX(0); }
        }
      `}</style>
    </div>
  );
}
