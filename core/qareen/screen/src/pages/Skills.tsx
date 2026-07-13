import { useState, useMemo } from 'react';
import {
  X, Search, Brain, GitBranch, Zap, Sparkles,
  Wrench, ChevronRight, Loader2,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { useSkills, useSkill } from '@/hooks/useSkills';
import type { Skill } from '@/hooks/useSkills';
import { useAgents } from '@/hooks/useAgents';
import type { Agent } from '@/hooks/useAgents';

// ============================================================
// Constants
// ============================================================

const CATEGORIES = ['all', 'core', 'workflow', 'domain', 'integration'] as const;
type Category = (typeof CATEGORIES)[number];

const CATEGORY_META: Record<string, { label: string; color: string; icon: ReactNode }> = {
  core:        { label: 'Core',        color: 'text-accent',  icon: <Brain className="w-3.5 h-3.5" /> },
  workflow:    { label: 'Workflow',    color: 'text-purple',  icon: <GitBranch className="w-3.5 h-3.5" /> },
  domain:      { label: 'Domain',     color: 'text-blue',    icon: <Sparkles className="w-3.5 h-3.5" /> },
  integration: { label: 'Integration', color: 'text-teal',   icon: <Zap className="w-3.5 h-3.5" /> },
};

const GLASS: React.CSSProperties = {
  background: 'var(--glass-bg, rgba(30, 26, 22, 0.60))',
  backdropFilter: 'blur(12px)',
  WebkitBackdropFilter: 'blur(12px)',
  borderColor: 'var(--glass-border, rgba(255, 245, 235, 0.06))',
  boxShadow: 'var(--glass-shadow, 0 2px 12px rgba(0,0,0,0.3))',
};

// ============================================================
// Skill Row
// ============================================================

function AgentPill({ agent }: { agent: Agent }) {
  return (
    <span
      className="inline-flex items-center gap-1 text-[10px] font-[510] rounded-full px-1.5 py-px"
      style={{ backgroundColor: (agent.color || '#6B6560') + '12', color: agent.color || '#6B6560' }}
    >
      <span className="text-[9px] font-[600]">{agent.initials || agent.name.slice(0, 2).toUpperCase()}</span>
      {agent.name}
    </span>
  );
}

function SkillRow({ skill, agents, totalAgents, onClick }: { skill: Skill; agents: Agent[]; totalAgents: number; onClick: () => void }) {
  const cat = CATEGORY_META[skill.category] ?? CATEGORY_META.domain;
  return (
    <button
      onClick={onClick}
      className="w-full flex items-center gap-4 px-4 py-3 rounded-lg hover:bg-[rgba(255,245,235,0.03)] transition-colors cursor-pointer group text-left"
      style={{ transitionDuration: '80ms' }}
    >
      <span className="text-text-quaternary shrink-0">{cat.icon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[13px] font-[560] text-text tracking-[-0.01em]">{skill.name}</span>
          <span className={`text-[10px] font-[510] ${cat.color}`}>{skill.category}</span>
        </div>
        <p className="text-[11px] text-text-quaternary leading-[1.5] mt-0.5 line-clamp-1">{skill.description}</p>
        <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
          {agents.length > 0 ? (
            agents.map(a => <AgentPill key={a.id} agent={a} />)
          ) : (
            <span className="text-[10px] text-text-quaternary/50">
              Available to all {totalAgents} agents
            </span>
          )}
        </div>
      </div>
      <div className="flex items-center gap-2.5 shrink-0">
        {skill.triggers.length > 0 && (
          <span className="text-[10px] text-text-quaternary/50 font-mono hidden sm:block truncate max-w-[120px]">
            {skill.triggers[0]}
          </span>
        )}
        <ChevronRight className="w-3.5 h-3.5 text-text-quaternary/30 group-hover:text-text-quaternary transition-colors" />
      </div>
    </button>
  );
}

// ============================================================
// Detail Panel
// ============================================================

function SkillDetail({ skillId, agents, totalAgents, onClose }: { skillId: string; agents: Agent[]; totalAgents: number; onClose: () => void }) {
  const { data: skill, isLoading } = useSkill(skillId);

  return (
    <div className="fixed inset-0 z-50" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
      <div
        className="absolute right-0 top-0 bottom-0 w-full max-w-[500px] bg-bg-panel overflow-y-auto animate-[slideIn_180ms_ease-out]"
        onClick={e => e.stopPropagation()}
      >
        <div className="p-6 sm:p-8">
          <button onClick={onClose} className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-hover text-text-quaternary cursor-pointer mb-6">
            <X className="w-4 h-4" />
          </button>

          {isLoading || !skill ? (
            <div className="flex items-center justify-center py-20">
              <Loader2 className="w-5 h-5 text-text-quaternary animate-spin" />
            </div>
          ) : (
            <>
              {/* Header */}
              <div className="mb-6">
                <div className="flex items-center gap-3 mb-2">
                  <span className="text-text-quaternary">
                    {(CATEGORY_META[skill.category] ?? CATEGORY_META.domain).icon}
                  </span>
                  <h2 className="text-[20px] font-[650] text-text tracking-[-0.02em]">{skill.name}</h2>
                </div>
                <span className={`text-[11px] font-[510] ${(CATEGORY_META[skill.category] ?? CATEGORY_META.domain).color}`}>
                  {skill.category}
                </span>
              </div>

              {/* Description */}
              <p className="text-[13px] text-text-secondary leading-[1.65] mb-6">{skill.description}</p>

              {/* Triggers */}
              {skill.triggers.length > 0 && (
                <Section title="Triggers">
                  <div className="flex flex-wrap gap-1.5">
                    {skill.triggers.map(t => (
                      <span key={t} className="text-[11px] font-mono text-accent/80 bg-accent/8 rounded px-2 py-0.5">{t}</span>
                    ))}
                  </div>
                </Section>
              )}

              {/* Agents with access */}
              <Section title="Availability">
                {agents.length > 0 ? (
                  <div className="space-y-1.5">
                    <p className="text-[11px] text-text-quaternary mb-2">Declared by these agents:</p>
                    {agents.map(a => (
                      <div key={a.id} className="flex items-center gap-2.5 py-1.5 px-2 rounded-md bg-[rgba(255,245,235,0.03)]">
                        <div
                          className="w-6 h-6 rounded flex items-center justify-center text-[9px] font-[600] shrink-0"
                          style={{ backgroundColor: (a.color || '#6B6560') + '15', color: a.color || '#6B6560' }}
                        >
                          {a.initials || a.name.slice(0, 2).toUpperCase()}
                        </div>
                        <div className="flex-1 min-w-0">
                          <span className="text-[12px] font-[510] text-text block leading-tight">{a.name}</span>
                          <span className="text-[10px] text-text-quaternary">{a.role || a.model}</span>
                        </div>
                        {a.is_system && <span className="text-[8px] font-[510] text-purple/60 uppercase tracking-wider">sys</span>}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-[12px] text-text-tertiary">
                    Globally installed — available to all {totalAgents} agents via <span className="font-mono text-accent/70">~/.claude/skills/</span>
                  </p>
                )}
              </Section>

              {/* Tools */}
              {skill.allowed_tools.length > 0 && (
                <Section title="Allowed Tools">
                  <div className="flex flex-wrap gap-1.5">
                    {skill.allowed_tools.map(t => (
                      <span key={t} className="text-[11px] font-mono text-text-tertiary bg-[rgba(255,245,235,0.04)] rounded px-2 py-0.5">{t}</span>
                    ))}
                  </div>
                </Section>
              )}

              {/* Body — full SKILL.md content */}
              {skill.body && (
                <Section title="Documentation">
                  <div className="text-[12px] text-text-tertiary leading-[1.7] font-mono whitespace-pre-wrap break-words max-h-[50vh] overflow-y-auto pr-2 [&_h2]:text-[13px] [&_h2]:font-[600] [&_h2]:text-text-secondary [&_h2]:mt-4 [&_h2]:mb-1">
                    {skill.body}
                  </div>
                </Section>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mb-5">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">{title}</span>
      </div>
      {children}
    </div>
  );
}

// ============================================================
// Main Page
// ============================================================

export default function SkillsPage() {
  const [searchQuery, setSearchQuery] = useState('');
  const [category, setCategory] = useState<Category>('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { data: skills = [], isLoading } = useSkills();
  const { data: agents = [] } = useAgents();

  // Build skill → agents map (from agents that declare skills in frontmatter)
  // Also include catalog agents for richer mapping
  const skillAgents = useMemo(() => {
    const map = new Map<string, Agent[]>();
    for (const agent of agents) {
      for (const skillId of agent.skills) {
        if (!map.has(skillId)) map.set(skillId, []);
        map.get(skillId)!.push(agent);
      }
    }
    return map;
  }, [agents]);

  // Count of installed agents (skills are globally available to all)
  const installedCount = agents.length;

  const filtered = useMemo(() => {
    let result = skills;
    if (category !== 'all') result = result.filter(s => s.category === category);
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      result = result.filter(s =>
        s.name.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q) ||
        s.triggers.some(t => t.toLowerCase().includes(q))
      );
    }
    return result;
  }, [skills, category, searchQuery]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: skills.length };
    for (const s of skills) c[s.category] = (c[s.category] ?? 0) + 1;
    return c;
  }, [skills]);

  return (
    <div className="h-full flex flex-col bg-bg">
      {/* Glass pill — category filter */}
      <div className="shrink-0 flex justify-center pt-3 pb-2 pointer-events-none">
        <div className="flex items-center gap-1 h-8 px-1 rounded-full border pointer-events-auto" style={GLASS}>
          {CATEGORIES.map(cat => (
            <button
              key={cat}
              onClick={() => setCategory(cat)}
              className={`px-3 h-6 rounded-full text-[12px] font-[510] cursor-pointer transition-all duration-150 ${
                category === cat
                  ? 'bg-[rgba(255,245,235,0.10)] text-text'
                  : 'text-text-tertiary hover:text-text-secondary'
              }`}
            >
              {cat === 'all' ? `All (${counts.all ?? 0})` : `${CATEGORY_META[cat]?.label} (${counts[cat] ?? 0})`}
            </button>
          ))}
        </div>
      </div>

      {/* Search */}
      <div className="shrink-0 px-6 sm:px-10 pb-2 pt-1">
        <div className="max-w-[680px] mx-auto">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-quaternary" />
            <input
              type="text"
              placeholder="Search skills..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="w-full h-8 pl-8 pr-3 rounded-full bg-transparent border border-border text-[12px] text-text placeholder:text-text-quaternary outline-none focus:border-border-secondary transition-colors"
            />
          </div>
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto px-4 sm:px-8 pb-8">
        <div className="max-w-[680px] mx-auto">
          {isLoading ? (
            <div className="flex items-center justify-center py-20">
              <Loader2 className="w-5 h-5 text-text-quaternary animate-spin" />
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center py-20">
              <Sparkles className="w-8 h-8 text-text-quaternary/20 mb-3" />
              <p className="text-[13px] text-text-tertiary">No skills match your search</p>
            </div>
          ) : (
            filtered.map(skill => (
              <SkillRow key={skill.id} skill={skill} agents={skillAgents.get(skill.id) ?? []} totalAgents={installedCount} onClick={() => setSelectedId(skill.id)} />
            ))
          )}
        </div>
      </div>

      {/* Detail panel */}
      {selectedId && (
        <SkillDetail skillId={selectedId} agents={skillAgents.get(selectedId) ?? []} totalAgents={installedCount} onClose={() => setSelectedId(null)} />
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
