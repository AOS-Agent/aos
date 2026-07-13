import { useState, useEffect, useMemo, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import {
  ArrowLeft, Brain, Shield, Users, Globe, Wrench,
  AlertTriangle, Code, Loader2, Heart,
} from 'lucide-react';
import TransitionLink from '@/components/primitives/TransitionLink';
import TagPicker from '@/components/agents/TagPicker';
import TrustSlider from '@/components/agents/TrustSlider';
import SelectField from '@/components/agents/SelectField';
import NumberField from '@/components/agents/NumberField';
import KeyValueEditor from '@/components/agents/KeyValueEditor';
import {
  useAgentConfig, useSaveConfig, useAgentOptions, useAgentHealth,
  useProviderModels,
  type AgentConfig,
} from '@/hooks/useAgentConfig';
import { useConnectors, type ConnectorInfo } from '@/hooks/useConnectors';
import type { ReactNode } from 'react';

// ============================================================
// Constants
// ============================================================

const PERMISSION_OPTIONS = [
  { value: 'default', label: 'Default — ask for risky actions' },
  { value: 'plan', label: 'Plan — require plan approval first' },
  { value: 'acceptEdits', label: 'Accept Edits — auto-approve file changes' },
  { value: 'bypassPermissions', label: 'Bypass — skip all permission checks' },
];

const EFFORT_OPTIONS = [
  { value: '', label: 'Inherit (session default)' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'max', label: 'Max (Opus only)' },
];

const ISOLATION_OPTIONS = [
  { value: '', label: 'None — shared working directory' },
  { value: 'worktree', label: 'Worktree — isolated git copy' },
];

const MEMORY_OPTIONS = [
  { value: '', label: 'None' },
  { value: 'user', label: 'User — persists across all projects' },
  { value: 'project', label: 'Project — per-project memory' },
  { value: 'local', label: 'Local — this directory only' },
];

const SCOPE_OPTIONS = [
  { value: 'global', label: 'Global — available everywhere' },
  { value: 'project', label: 'Project — specific directory only' },
];

const FAILURE_OPTIONS = [
  { value: 'escalate', label: 'Escalate to parent' },
  { value: 'retry', label: 'Retry automatically' },
  { value: 'degrade', label: 'Degrade gracefully' },
];

// ============================================================
// Tabs
// ============================================================

const TABS = [
  { id: 'capabilities', label: 'Capabilities', icon: <Brain className="w-3 h-3" /> },
  { id: 'permissions', label: 'Permissions', icon: <Shield className="w-3 h-3" /> },
  { id: 'orchestration', label: 'Orchestration', icon: <Users className="w-3 h-3" /> },
  { id: 'context', label: 'Context', icon: <Globe className="w-3 h-3" /> },
  { id: 'prompt', label: 'Prompt', icon: <Code className="w-3 h-3" /> },
] as const;

type TabId = (typeof TABS)[number]['id'];

const GLASS: React.CSSProperties = {
  background: 'rgba(30, 26, 22, 0.60)',
  backdropFilter: 'blur(12px)',
  WebkitBackdropFilter: 'blur(12px)',
  borderColor: 'rgba(255, 245, 235, 0.06)',
  boxShadow: '0 2px 12px rgba(0,0,0,0.3)',
};

// ============================================================
// Field wrapper
// ============================================================

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div>
      <label className="block text-[11px] font-[510] text-text-quaternary mb-1.5">{label}</label>
      {children}
      {hint && <p className="text-[10px] text-text-quaternary/60 mt-1.5 leading-[1.4]">{hint}</p>}
    </div>
  );
}

// ============================================================
// Tool access (allow / block / all)
// ============================================================

type ToolMode = 'all' | 'allow' | 'block';

function ToolsField({ tools, disallowed, available, onToolsChange, onDisallowedChange, disabled }: {
  tools: string[]; disallowed: string[]; available: string[];
  onToolsChange: (v: string[]) => void; onDisallowedChange: (v: string[]) => void;
  disabled?: boolean;
}) {
  const hasWildcard = tools.includes('*');
  const hasAllowlist = tools.length > 0 && !hasWildcard;
  const hasBlocklist = disallowed.length > 0;
  const initialMode: ToolMode = hasWildcard ? (hasBlocklist ? 'block' : 'all') : hasAllowlist ? 'allow' : hasBlocklist ? 'block' : 'all';
  const [mode, setMode] = useState<ToolMode>(initialMode);

  const handleMode = (m: ToolMode) => {
    setMode(m);
    if (m === 'all') { onToolsChange(['*']); onDisallowedChange([]); }
    else if (m === 'allow') { if (hasWildcard) onToolsChange([]); onDisallowedChange([]); }
    else { onToolsChange(['*']); }
  };

  return (
    <Field label="Tool access">
      <div className="flex items-center gap-0.5 mb-3 bg-[rgba(255,245,235,0.03)] rounded-lg p-0.5 w-fit">
        {(['all', 'allow', 'block'] as const).map(k => (
          <button key={k} onClick={() => !disabled && handleMode(k)}
            className={`h-6 px-2.5 rounded-md text-[11px] font-[510] cursor-pointer transition-all duration-100 ${mode === k ? 'bg-[rgba(255,245,235,0.08)] text-text' : 'text-text-quaternary hover:text-text-tertiary'} ${disabled ? 'opacity-50 cursor-default' : ''}`}>
            {k === 'all' ? 'All tools' : k === 'allow' ? 'Allow specific' : 'Block specific'}
          </button>
        ))}
      </div>
      {mode === 'all' && <p className="text-[12px] text-text-tertiary">Inherits all available tools.</p>}
      {mode === 'allow' && <TagPicker selected={hasWildcard ? [] : tools} available={available} onChange={onToolsChange} disabled={disabled} />}
      {mode === 'block' && <TagPicker selected={disallowed} available={available} onChange={onDisallowedChange} disabled={disabled} />}
    </Field>
  );
}

// ============================================================
// Health indicator
// ============================================================

function HealthBadge({ id }: { id: string }) {
  const { data: health } = useAgentHealth(id);
  if (!health) return null;
  const failed = health.checks.filter(c => !c.ok);
  if (failed.length === 0) return (
    <span className="inline-flex items-center gap-1 text-[10px] text-green font-[510]">
      <span className="w-1.5 h-1.5 rounded-full bg-green" /> Healthy
    </span>
  );
  return (
    <span className="inline-flex items-center gap-1 text-[10px] text-yellow font-[510]" title={failed.map(f => f.message).join('\n')}>
      <span className="w-1.5 h-1.5 rounded-full bg-yellow" /> {failed.length} issue{failed.length > 1 ? 's' : ''}
    </span>
  );
}

// ============================================================
// Helpers
// ============================================================

function formDiffers(form: Partial<AgentConfig>, original: AgentConfig): boolean {
  for (const key of Object.keys(form) as (keyof AgentConfig)[]) {
    const a = form[key], b = original[key];
    if (a === b) continue;
    if (typeof a === 'object' && typeof b === 'object' && a !== null && b !== null) {
      if (JSON.stringify(a) !== JSON.stringify(b)) return true;
    } else if (a !== b) return true;
  }
  return false;
}

function AgentAvatar({ name, initials, color }: { name: string; initials: string; color: string }) {
  return (
    <div className="w-12 h-12 rounded-xl flex items-center justify-center shrink-0 text-[16px] font-[600]"
      style={{ backgroundColor: (color || '#6B6560') + '15', color: color || '#6B6560' }}>
      {initials || name.slice(0, 2).toUpperCase()}
    </div>
  );
}

// ============================================================
// Main page
// ============================================================

export default function AgentConfigPage() {
  const { id } = useParams<{ id: string }>();
  const { data: config, isLoading, error } = useAgentConfig(id ?? null);
  const options = useAgentOptions();
  const saveMutation = useSaveConfig();
  const providerModels = useProviderModels();
  const { connectors: connectorList } = useConnectors();

  // Build display name labels for MCP servers and skills
  const mcpLabels = useMemo(() => {
    const labels: Record<string, string> = {};
    for (const c of connectorList as ConnectorInfo[]) {
      if (c.id && c.name && c.id !== c.name) {
        labels[c.id] = c.name;
      }
    }
    return labels;
  }, [connectorList]);

  const skillLabels = useMemo(() => {
    const labels: Record<string, string> = {};
    for (const s of options.skills) {
      const pretty = s.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
      if (pretty !== s) labels[s] = pretty;
    }
    return labels;
  }, [options.skills]);
  const modelOptions = useMemo(
    () => providerModels.map(m => ({ value: m.value, label: m.label })),
    [providerModels],
  );

  const [tab, setTab] = useState<TabId>('capabilities');
  const [form, setForm] = useState<Partial<AgentConfig>>({});
  const [initialized, setInitialized] = useState(false);

  useEffect(() => {
    if (config && !initialized) {
      setForm({
        model: config.model, tools: config.tools, disallowed_tools: config.disallowed_tools,
        skills: config.skills, mcp_servers: config.mcp_servers,
        default_trust: config.default_trust, permission_mode: config.permission_mode,
        max_turns: config.max_turns, effort: config.effort,
        reports_to: config.reports_to, can_spawn: config.can_spawn,
        isolation: config.isolation, background: config.background, memory: config.memory,
        scope: config.scope, rules: config.rules, services: config.services,
        parameters: config.parameters, on_failure: config.on_failure, max_retries: config.max_retries,
        body: config.body,
      });
      setInitialized(true);
    }
  }, [config, initialized]);

  useEffect(() => { setInitialized(false); setForm({}); }, [id]);

  const isDirty = useMemo(() => config && initialized ? formDiffers(form, config) : false, [form, config, initialized]);
  const isSystem = config?.is_system ?? false;
  const disabled = isSystem;

  const reportsToOptions = useMemo(() => {
    const opts = [{ value: '', label: '(none)' }];
    for (const a of options.agents) { if (a !== id) opts.push({ value: a, label: a }); }
    return opts;
  }, [options.agents, id]);

  const updateField = useCallback(<K extends keyof AgentConfig>(key: K, value: AgentConfig[K]) => {
    setForm(prev => ({ ...prev, [key]: value }));
  }, []);

  const handleSave = useCallback(() => {
    if (!id || !isDirty || isSystem) return;
    saveMutation.mutate({ id, patch: form });
  }, [id, isDirty, isSystem, form, saveMutation]);

  const handleCancel = useCallback(() => {
    if (!config) return;
    setInitialized(false);
    setTimeout(() => setInitialized(false), 0); // force re-init
  }, [config]);

  // Reset initialized to re-populate form from config
  useEffect(() => {
    if (!initialized && config) {
      setForm({
        model: config.model, tools: config.tools, disallowed_tools: config.disallowed_tools,
        skills: config.skills, mcp_servers: config.mcp_servers,
        default_trust: config.default_trust, permission_mode: config.permission_mode,
        max_turns: config.max_turns, effort: config.effort,
        reports_to: config.reports_to, can_spawn: config.can_spawn,
        isolation: config.isolation, background: config.background, memory: config.memory,
        scope: config.scope, rules: config.rules, services: config.services,
        parameters: config.parameters, on_failure: config.on_failure, max_retries: config.max_retries,
        body: config.body,
      });
      setInitialized(true);
    }
  }, [config, initialized]);

  // Loading / error
  if (isLoading) return <div className="h-full flex items-center justify-center"><Loader2 className="w-5 h-5 text-text-quaternary animate-spin" /></div>;
  if (error || !config) return (
    <div className="h-full flex flex-col items-center justify-center gap-3">
      <AlertTriangle className="w-6 h-6 text-red/60" />
      <p className="text-[13px] text-text-tertiary">{error ? `Failed to load: ${(error as Error).message}` : 'Agent not found'}</p>
      <TransitionLink href="/agents" className="text-[12px] font-[510] text-accent hover:text-accent-hover transition-colors">Back to Agents</TransitionLink>
    </div>
  );

  return (
    <div className="h-full flex flex-col bg-bg">
      {/* Header */}
      <div className="shrink-0 px-6 pt-6 pb-4">
        <div className="max-w-[640px] mx-auto">
          <TransitionLink href="/agents" className="inline-flex items-center gap-1.5 text-[12px] font-[510] text-text-quaternary hover:text-text-tertiary transition-colors mb-5 cursor-pointer">
            <ArrowLeft className="w-3.5 h-3.5" /> Back to Agents
          </TransitionLink>

          <div className="flex items-center gap-4">
            <AgentAvatar name={config.name} initials={config.initials} color={config.color} />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2.5">
                <h2 className="text-[18px] font-[650] text-text tracking-[-0.02em]">{config.name}</h2>
                {isSystem && <span className="text-[9px] font-[510] text-purple/70 uppercase tracking-wider bg-purple/8 px-1.5 py-0.5 rounded">system</span>}
                <HealthBadge id={id!} />
              </div>
              <p className="text-[12px] text-text-tertiary mt-0.5">{config.role}</p>
            </div>
            <div className="text-right shrink-0">
              <span className="text-[11px] font-mono text-text-quaternary block">{config.model}</span>
              <span className="text-[10px] text-text-quaternary">{config.source} · v{config.version}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Tab pills */}
      <div className="shrink-0 flex justify-center pb-3 pointer-events-none">
        <div className="flex items-center gap-1 h-8 px-1 rounded-full border pointer-events-auto" style={GLASS}>
          {TABS.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-3 h-6 rounded-full text-[11px] font-[510] cursor-pointer transition-all duration-150 ${
                tab === t.id ? 'bg-[rgba(255,245,235,0.10)] text-text' : 'text-text-tertiary hover:text-text-secondary'
              }`}>
              {t.icon} {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* System banner */}
      {isSystem && (
        <div className="px-6">
          <div className="max-w-[640px] mx-auto rounded-lg border border-purple/20 bg-purple-muted px-4 py-2.5 mb-2">
            <p className="text-[11px] font-[510] text-purple/70">System agent — read-only. Managed by the framework.</p>
          </div>
        </div>
      )}

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto px-6 pb-20">
        <div className="max-w-[640px] mx-auto pt-4 space-y-6">

          {tab === 'capabilities' && (<>
            <SelectField label="Model" value={form.model ?? config.model} options={modelOptions}
              onChange={v => updateField('model', v)} disabled={disabled} />

            <ToolsField tools={form.tools ?? config.tools} disallowed={form.disallowed_tools ?? config.disallowed_tools}
              available={options.tools} onToolsChange={v => updateField('tools', v)}
              onDisallowedChange={v => updateField('disallowed_tools', v)} disabled={disabled} />

            <Field label="Skills" hint="Skills loaded into the agent's context at startup.">
              <TagPicker selected={form.skills ?? config.skills} available={options.skills}
                onChange={v => updateField('skills', v)} disabled={disabled} labels={skillLabels} />
            </Field>

            <Field label="MCP servers" hint="External tool servers this agent can access.">
              <TagPicker selected={form.mcp_servers ?? config.mcp_servers} available={options.mcpServers}
                onChange={v => updateField('mcp_servers', v)} disabled={disabled} labels={mcpLabels} />
            </Field>
          </>)}

          {tab === 'permissions' && (<>
            <Field label="Trust level" hint="Controls how much autonomy this agent has. Higher levels mean less human oversight.">
              <TrustSlider value={form.default_trust ?? config.default_trust}
                onChange={v => updateField('default_trust', v)} disabled={disabled} />
            </Field>

            <SelectField label="Permission mode" value={form.permission_mode ?? config.permission_mode}
              options={PERMISSION_OPTIONS} onChange={v => updateField('permission_mode', v)} disabled={disabled} />

            <NumberField label="Max turns" value={form.max_turns !== undefined ? form.max_turns : config.max_turns}
              onChange={v => updateField('max_turns', v)} min={1} max={1000} disabled={disabled} />

            <SelectField label="Effort" value={form.effort ?? config.effort}
              options={EFFORT_OPTIONS} onChange={v => updateField('effort', v)} disabled={disabled} />

            <Field label="Failure behavior">
              <div className="flex gap-3">
                <div className="flex-1">
                  <SelectField label="" value={form.on_failure ?? config.on_failure}
                    options={FAILURE_OPTIONS} onChange={v => updateField('on_failure', v)} disabled={disabled} />
                </div>
                <div className="w-[120px]">
                  <NumberField label="Max retries" value={form.max_retries !== undefined ? form.max_retries : config.max_retries}
                    onChange={v => updateField('max_retries', v ?? 0)} min={0} max={10} disabled={disabled} />
                </div>
              </div>
            </Field>
          </>)}

          {tab === 'orchestration' && (<>
            <SelectField label="Reports to" value={form.reports_to ?? config.reports_to ?? ''}
              options={reportsToOptions} onChange={v => updateField('reports_to', v || null)} disabled={disabled} />

            <Field label="Can spawn" hint="Sub-agents this agent can dispatch. Maps to tools: Agent(x,y) in the .md file.">
              <TagPicker selected={form.can_spawn ?? config.can_spawn}
                available={options.agents.filter(a => a !== id)} onChange={v => updateField('can_spawn', v)} disabled={disabled} />
            </Field>

            <SelectField label="Isolation" value={form.isolation ?? config.isolation}
              options={ISOLATION_OPTIONS} onChange={v => updateField('isolation', v)} disabled={disabled} />

            <div className="flex items-center justify-between py-1">
              <div>
                <label className="text-[11px] font-[510] text-text-quaternary block">Run in background</label>
                <p className="text-[10px] text-text-quaternary/60">Agent runs async — doesn't block the caller.</p>
              </div>
              <button type="button" disabled={disabled}
                onClick={() => updateField('background', !(form.background ?? config.background))}
                className={`w-9 h-5 rounded-full transition-colors cursor-pointer disabled:cursor-default ${(form.background ?? config.background) ? 'bg-accent' : 'bg-[rgba(255,245,235,0.10)]'}`}
                style={{ transitionDuration: '80ms' }}>
                <div className={`w-3.5 h-3.5 rounded-full bg-white shadow-sm transition-transform duration-150 ${(form.background ?? config.background) ? 'translate-x-[18px]' : 'translate-x-[3px]'}`} />
              </button>
            </div>

            <SelectField label="Memory" value={form.memory ?? config.memory}
              options={MEMORY_OPTIONS} onChange={v => updateField('memory', v)} disabled={disabled} />
          </>)}

          {tab === 'context' && (<>
            <SelectField label="Scope" value={form.scope ?? config.scope}
              options={SCOPE_OPTIONS} onChange={v => updateField('scope', v)} disabled={disabled} />

            <Field label="Rules" hint="Claude Code rules loaded into this agent's context.">
              <TagPicker selected={form.rules ?? config.rules} available={options.rules}
                onChange={v => updateField('rules', v)} disabled={disabled} />
            </Field>

            <Field label="Services" hint="AOS services this agent depends on.">
              <TagPicker selected={form.services ?? config.services} available={options.services}
                onChange={v => updateField('services', v)} disabled={disabled} />
            </Field>

            <Field label="Parameters" hint="Operator-tunable configuration. Key-value pairs injected into the agent's context.">
              <KeyValueEditor entries={(form.parameters ?? config.parameters) as Record<string, string>}
                onChange={v => updateField('parameters', v)} disabled={disabled} />
            </Field>
          </>)}

          {tab === 'prompt' && (<>
            <p className="text-[12px] text-text-tertiary mb-3">
              The system prompt — the markdown body after YAML frontmatter. This defines who the agent is and how it behaves.
            </p>
            <textarea
              value={form.body ?? config.body}
              onChange={e => updateField('body', e.target.value)}
              disabled={disabled}
              className="w-full min-h-[400px] px-4 py-3 rounded-lg border border-border-secondary bg-bg-secondary text-[12px] font-mono leading-[1.7] text-text-secondary placeholder:text-text-quaternary outline-none resize-y disabled:opacity-50 disabled:cursor-default transition-colors focus:border-border-tertiary"
              style={{ transitionDuration: '80ms' }}
            />
          </>)}

        </div>
      </div>

      {/* Sticky save bar */}
      {isDirty && !isSystem && (
        <div className="fixed bottom-0 left-0 right-0 z-40 flex justify-center pb-4 pointer-events-none">
          <div className="pointer-events-auto flex items-center gap-4 h-11 px-5 rounded-full border border-border" style={{
            background: 'rgba(30, 26, 22, 0.70)', backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)',
            boxShadow: '0 4px 24px rgba(0,0,0,0.5)',
          }}>
            <span className="text-[12px] font-[510] text-text-secondary">Unsaved changes</span>
            <button type="button" onClick={handleCancel}
              className="h-7 px-3 rounded-md text-[12px] font-[510] text-text-tertiary hover:text-text-secondary hover:bg-hover cursor-pointer transition-colors" style={{ transitionDuration: '80ms' }}>
              Cancel
            </button>
            <button type="button" onClick={handleSave} disabled={saveMutation.isPending}
              className="h-7 px-4 rounded-md bg-accent hover:bg-accent-hover text-[12px] font-[510] text-white cursor-pointer transition-colors disabled:opacity-60 disabled:cursor-not-allowed" style={{ transitionDuration: '80ms' }}>
              {saveMutation.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : 'Save'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
