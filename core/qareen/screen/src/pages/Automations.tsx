import { useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Zap, Plus, Play, ChevronDown, ChevronRight,
  MoreHorizontal, X, Check, Clock, AlertCircle,
  Pause, Send, Loader2, Sparkles, ArrowRight,
  MessageCircle, Mail, Sheet, Rss, Webhook,
  Calendar, Globe, Bot,
} from 'lucide-react';
import { Tag, StatusDot } from '@/components/primitives';
import {
  useN8nAutomations, useAutomationsHealth,
  useGenerateAutomation, useDeployAutomation,
  useActivateAutomation, useDeactivateAutomation,
  type N8nAutomation, type GenerateResult,
} from '@/hooks/useAutomations';

// ---------------------------------------------------------------------------
// Automations — visual control surface for scheduled jobs.
// Shows user-created automations + system crons.
// ---------------------------------------------------------------------------

interface Automation {
  id: string;
  name: string;
  description: string;
  prompt?: string;
  frequency: string;
  at?: string;
  weekday?: string;
  every?: string;
  agent?: string;
  enabled: boolean;
  notify?: boolean;
  type: 'user' | 'system';
  tier?: number;
  tier_label?: string;
  schedule_human: string;
  last_run?: string;
  last_run_ago?: string;
  last_status?: string | number;
  duration_ms?: number;
  created?: string;
}

interface AutomationsData {
  user: Automation[];
  system: Automation[];
  total: number;
}

function useAutomations() {
  return useQuery({
    queryKey: ['automations'],
    queryFn: async (): Promise<AutomationsData> => {
      const res = await fetch('/api/automations');
      if (!res.ok) throw new Error('Failed');
      return res.json();
    },
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

// ── Status helpers ──

function statusColor(status: string | number | undefined): string {
  if (status === undefined || status === null) return 'gray';
  if (status === 'success' || status === 0) return 'green';
  if (status === 'running') return 'blue';
  return 'red';
}

function statusLabel(status: string | number | undefined): string {
  if (status === undefined || status === null) return 'Never run';
  if (status === 'success' || status === 0) return 'Success';
  if (status === 'running') return 'Running';
  if (status === 'timeout') return 'Timeout';
  return `Error (${status})`;
}

// ── Automation row ──

function AutomationRow({
  automation,
  onToggle,
  onRun,
  onClick,
}: {
  automation: Automation;
  onToggle: () => void;
  onRun: () => void;
  onClick: () => void;
}) {
  const sc = statusColor(automation.last_status);

  return (
    <div
      onClick={onClick}
      className="flex items-center gap-3 py-3 px-1 cursor-pointer hover:bg-hover rounded-[5px] transition-colors duration-100 group"
    >
      <StatusDot color={automation.enabled ? sc : 'gray'} size="md" />

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-[510] text-text-secondary">
            {automation.name}
          </span>
          {!automation.enabled && (
            <Tag label="Paused" color="gray" size="sm" />
          )}
        </div>
        <span className="text-[11px] text-text-quaternary block mt-0.5 truncate">
          {automation.schedule_human}
          {automation.last_run_ago && ` · ${automation.last_run_ago}`}
        </span>
      </div>

      {/* Quick actions — visible on hover */}
      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-100">
        <button
          onClick={(e) => { e.stopPropagation(); onRun(); }}
          title="Run now"
          className="w-7 h-7 flex items-center justify-center rounded-[5px] text-text-quaternary hover:text-accent hover:bg-bg-tertiary transition-colors cursor-pointer"
        >
          <Play className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onToggle(); }}
          title={automation.enabled ? 'Pause' : 'Resume'}
          className="w-7 h-7 flex items-center justify-center rounded-[5px] text-text-quaternary hover:text-text-tertiary hover:bg-bg-tertiary transition-colors cursor-pointer"
        >
          {automation.enabled
            ? <span className="w-3 h-3 rounded-sm border-2 border-current" />
            : <Play className="w-3 h-3" />
          }
        </button>
      </div>
    </div>
  );
}

// ── Detail panel ──

function DetailPanel({
  automation,
  onClose,
  onRun,
  onToggle,
}: {
  automation: Automation;
  onClose: () => void;
  onRun: () => void;
  onToggle: () => void;
}) {
  const sc = statusColor(automation.last_status);

  return (
    <div className="border-t border-border mt-2 pt-4 pb-2 animate-[fadeIn_150ms_ease-out]">
      {/* Header */}
      <div className="flex items-start justify-between mb-4">
        <div>
          <h2 className="text-[18px] font-[600] text-text">{automation.name}</h2>
          <div className="flex items-center gap-2 mt-1">
            <Tag
              label={automation.enabled ? 'Active' : 'Paused'}
              color={automation.enabled ? 'green' : 'gray'}
              size="sm"
            />
            <span className="text-[11px] text-text-quaternary">
              {automation.last_run_ago ? `Last run ${automation.last_run_ago}` : 'Never run'}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onRun}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-[5px] text-[12px] font-[510] bg-bg-tertiary border border-border text-text-secondary hover:bg-bg-quaternary transition-colors cursor-pointer"
          >
            <Play className="w-3 h-3" /> Run now
          </button>
          <button
            onClick={onClose}
            className="w-7 h-7 flex items-center justify-center rounded-[5px] text-text-quaternary hover:text-text-tertiary hover:bg-hover transition-colors cursor-pointer"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Content grid */}
      <div className="grid grid-cols-2 gap-6">
        <div>
          <span className="text-[11px] font-[510] text-text-quaternary block mb-1">Description</span>
          <p className="text-[13px] text-text-secondary">
            {automation.description || 'No description'}
          </p>
        </div>
        {automation.prompt && (
          <div>
            <span className="text-[11px] font-[510] text-text-quaternary block mb-1">Instructions</span>
            <p className="text-[13px] text-text-tertiary bg-bg-tertiary rounded-[5px] px-2.5 py-2">
              {automation.prompt}
            </p>
          </div>
        )}
      </div>

      {/* Schedule + status */}
      <div className="mt-4 flex items-center gap-4">
        <div className="flex items-center gap-2">
          <Clock className="w-3.5 h-3.5 text-text-quaternary" />
          <span className="text-[12px] text-text-tertiary">{automation.schedule_human}</span>
        </div>
        <div className="flex items-center gap-2">
          <StatusDot color={sc} size="sm" />
          <span className="text-[12px] text-text-tertiary">{statusLabel(automation.last_status)}</span>
        </div>
        {automation.duration_ms != null && (
          <span className="text-[11px] text-text-quaternary">
            {automation.duration_ms < 1000 ? `${automation.duration_ms}ms` : `${(automation.duration_ms / 1000).toFixed(1)}s`}
          </span>
        )}
      </div>

      {/* Toggle */}
      <div className="mt-4">
        <button
          onClick={onToggle}
          className="text-[12px] font-[510] text-text-quaternary hover:text-text-tertiary cursor-pointer transition-colors"
        >
          {automation.enabled ? 'Pause this automation' : 'Resume this automation'}
        </button>
      </div>

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(-4px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}

// ── Create modal ──

function CreateModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [prompt, setPrompt] = useState('');
  const [frequency, setFrequency] = useState('daily');
  const [at, setAt] = useState('07:00');

  const create = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/automations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, description, prompt, frequency, at }),
      });
      if (!res.ok) throw new Error('Failed');
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['automations'] });
      onClose();
    },
  });

  return (
    <div className="fixed inset-0 z-[600] flex items-center justify-center font-sans">
      <div className="absolute inset-0 bg-bg/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-[480px] max-w-[90vw] bg-bg-panel border border-border rounded-[10px] shadow-[0_8px_32px_rgba(0,0,0,0.5)] p-5">
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-[16px] font-[600] text-text">New automation</h2>
          <button onClick={onClose} className="p-1 rounded-[3px] text-text-quaternary hover:text-text-tertiary cursor-pointer">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="text-[11px] font-[510] text-text-quaternary block mb-1.5">Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Daily email digest"
              className="w-full h-8 px-2.5 rounded-[5px] bg-bg-tertiary border border-border text-[13px] text-text-secondary placeholder:text-text-quaternary hover:border-border-secondary focus:outline-none focus:border-accent/60"
            />
          </div>

          <div>
            <label className="text-[11px] font-[510] text-text-quaternary block mb-1.5">Description</label>
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What does this automation do?"
              className="w-full h-8 px-2.5 rounded-[5px] bg-bg-tertiary border border-border text-[13px] text-text-secondary placeholder:text-text-quaternary hover:border-border-secondary focus:outline-none focus:border-accent/60"
            />
          </div>

          <div>
            <label className="text-[11px] font-[510] text-text-quaternary block mb-1.5">Instructions</label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="What should the agent do? e.g. Check my Google Calendar for today's meetings and summarize my unread emails."
              rows={3}
              className="w-full px-2.5 py-2 rounded-[5px] bg-bg-tertiary border border-border text-[13px] text-text-secondary placeholder:text-text-quaternary hover:border-border-secondary focus:outline-none focus:border-accent/60 resize-none"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[11px] font-[510] text-text-quaternary block mb-1.5">Frequency</label>
              <select
                value={frequency}
                onChange={(e) => setFrequency(e.target.value)}
                className="w-full h-8 px-2.5 rounded-[5px] bg-bg-tertiary border border-border text-[13px] text-text-secondary cursor-pointer hover:border-border-secondary focus:outline-none focus:border-accent/60 appearance-none"
                style={{ backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%236B6560' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E")`, backgroundRepeat: 'no-repeat', backgroundPosition: 'right 8px center' }}
              >
                <option value="manual">Manual</option>
                <option value="hourly">Hourly</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </select>
            </div>
            {frequency !== 'manual' && (
              <div>
                <label className="text-[11px] font-[510] text-text-quaternary block mb-1.5">Time</label>
                <input
                  type="text"
                  value={at}
                  onChange={(e) => setAt(e.target.value)}
                  placeholder="07:00"
                  className="w-full h-8 px-2.5 rounded-[5px] bg-bg-tertiary border border-border text-[13px] text-text-secondary placeholder:text-text-quaternary hover:border-border-secondary focus:outline-none focus:border-accent/60"
                />
              </div>
            )}
          </div>
        </div>

        <div className="flex justify-end gap-2 mt-5">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded-[5px] text-[12px] font-[510] text-text-tertiary hover:text-text-secondary hover:bg-hover transition-colors cursor-pointer"
          >
            Cancel
          </button>
          <button
            onClick={() => create.mutate()}
            disabled={!name.trim() || create.isPending}
            className="px-3 py-1.5 rounded-[5px] text-[12px] font-[510] bg-accent text-white hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-40"
          >
            {create.isPending ? 'Creating...' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── System automations group (collapsible) ──

function SystemGroup({
  automations,
  onToggle,
  onRun,
  onSelect,
  selectedId,
}: {
  automations: Automation[];
  onToggle: (id: string, enabled: boolean) => void;
  onRun: (id: string) => void;
  onSelect: (a: Automation) => void;
  selectedId: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const Chevron = expanded ? ChevronDown : ChevronRight;

  return (
    <div className="mt-6">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full cursor-pointer mb-2"
      >
        <Chevron className="w-3 h-3 text-text-quaternary" />
        <span className="text-[11px] font-[590] text-text-quaternary uppercase tracking-[0.06em]">
          System
        </span>
        <span className="text-[10px] text-text-quaternary">{automations.length}</span>
      </button>
      {expanded && (
        <div>
          {automations.map((a) => (
            <div key={a.id}>
              <AutomationRow
                automation={a}
                onToggle={() => onToggle(a.id, !a.enabled)}
                onRun={() => onRun(a.id)}
                onClick={() => onSelect(a)}
              />
              {selectedId === a.id && (
                <DetailPanel
                  automation={a}
                  onClose={() => onSelect(a)}
                  onRun={() => onRun(a.id)}
                  onToggle={() => onToggle(a.id, !a.enabled)}
                />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Helpers for automation cards ──

const RECIPE_ICONS: Record<string, { trigger: typeof Clock; action: typeof Zap; triggerLabel: string; actionLabel: string }> = {
  schedule_to_telegram: { trigger: Clock, action: MessageCircle, triggerLabel: 'Schedule', actionLabel: 'Telegram' },
  scheduled_http_report: { trigger: Clock, action: Globe, triggerLabel: 'Schedule', actionLabel: 'HTTP' },
  rss_to_webhook: { trigger: Rss, action: Globe, triggerLabel: 'RSS Feed', actionLabel: 'Webhook' },
  webhook_relay: { trigger: Webhook, action: Globe, triggerLabel: 'Webhook', actionLabel: 'Forward' },
  form_to_sheets: { trigger: Webhook, action: Sheet, triggerLabel: 'Form', actionLabel: 'Sheets' },
};

function cronToHuman(cron: string): string {
  if (!cron) return '';
  const parts = cron.split(' ');
  if (parts.length < 5) return cron;
  const [min, hour, , , dow] = parts;
  const h = parseInt(hour);
  const m = parseInt(min);
  const suffix = h < 12 ? 'AM' : 'PM';
  const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
  const time = `${h12}:${m.toString().padStart(2, '0')} ${suffix}`;

  if (dow === '*') return `Daily at ${time}`;
  if (dow === '1-5') return `Weekdays at ${time}`;
  if (dow === '0') return `Sundays at ${time}`;
  if (dow === '1') return `Mondays at ${time}`;
  return `${time}`;
}

// ── n8n Automation card ──

function N8nAutomationCard({
  automation,
  onActivate,
  onDeactivate,
}: {
  automation: N8nAutomation;
  onActivate: () => void;
  onDeactivate: () => void;
}) {
  const statusColors: Record<string, string> = {
    active: 'green', draft: 'gray', paused: 'yellow', error: 'red', archived: 'gray',
  };
  const sc = statusColors[automation.status] || 'gray';
  const icons = RECIPE_ICONS[automation.recipe_id || ''] || { trigger: Zap, action: Zap, triggerLabel: 'Trigger', actionLabel: 'Action' };
  const TriggerIcon = icons.trigger;
  const ActionIcon = icons.action;

  const cronStr = (automation.trigger_config as Record<string, unknown>)?.cron as string || '';
  const schedule = cronToHuman(cronStr);

  return (
    <div
      className="rounded-[10px] border border-border p-4 mb-3 group transition-all duration-150 hover:border-border-secondary"
      style={{ background: 'var(--glass-bg)', backdropFilter: 'blur(12px)' }}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2.5">
          <StatusDot color={sc} size="md" pulse={automation.status === 'active'} />
          <span className="text-[14px] font-[560] text-text">{automation.name}</span>
          <Tag label={automation.status} color={sc as any} size="sm" />
        </div>
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          {automation.status === 'active' ? (
            <button
              onClick={onDeactivate}
              className="flex items-center gap-1 px-2 py-1 rounded-[5px] text-[11px] font-[510] text-text-quaternary hover:text-yellow-400 hover:bg-bg-tertiary transition-colors cursor-pointer"
            >
              <Pause className="w-3 h-3" /> Pause
            </button>
          ) : (
            <button
              onClick={onActivate}
              className="flex items-center gap-1 px-2 py-1 rounded-[5px] text-[11px] font-[510] text-text-quaternary hover:text-green-400 hover:bg-bg-tertiary transition-colors cursor-pointer"
            >
              <Play className="w-3 h-3" /> Activate
            </button>
          )}
        </div>
      </div>

      {/* Flow visualization */}
      <div className="flex items-center gap-2 mb-3">
        <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-[7px] bg-bg-tertiary border border-border">
          <TriggerIcon className="w-3.5 h-3.5 text-accent" />
          <span className="text-[11px] font-[510] text-text-secondary">{icons.triggerLabel}</span>
        </div>
        <ArrowRight className="w-3 h-3 text-text-quaternary" />
        <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-[7px] bg-bg-tertiary border border-border">
          <ActionIcon className="w-3.5 h-3.5 text-accent" />
          <span className="text-[11px] font-[510] text-text-secondary">{icons.actionLabel}</span>
        </div>
        {schedule && (
          <>
            <span className="text-text-quaternary mx-1">·</span>
            <span className="text-[11px] text-text-tertiary">{schedule}</span>
          </>
        )}
      </div>

      {/* Description */}
      <p className="text-[12px] text-text-quaternary leading-relaxed">
        {automation.description || automation.user_prompt}
      </p>

      {/* Footer stats */}
      <div className="flex items-center gap-4 mt-3 pt-2.5 border-t border-border">
        {automation.run_count > 0 && (
          <span className="text-[10px] text-text-quaternary">
            {automation.run_count} run{automation.run_count !== 1 ? 's' : ''}
          </span>
        )}
        {automation.last_run_at && (
          <span className="text-[10px] text-text-quaternary">
            Last: {new Date(automation.last_run_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}
          </span>
        )}
        {automation.error_message && (
          <span className="text-[10px] text-red-400 flex items-center gap-1">
            <AlertCircle className="w-3 h-3" /> {automation.error_message}
          </span>
        )}
        {automation.run_count === 0 && !automation.error_message && (
          <span className="text-[10px] text-text-quaternary">No runs yet</span>
        )}
      </div>
    </div>
  );
}

// ── Natural language creation flow ──

function GenerateFlow({ onClose }: { onClose: () => void }) {
  const [input, setInput] = useState('');
  const generate = useGenerateAutomation();
  const deploy = useDeployAutomation();
  const [preview, setPreview] = useState<GenerateResult | null>(null);

  const handleGenerate = () => {
    if (!input.trim()) return;
    generate.mutate({
      description: input,
      connected_accounts: ['telegram', 'google_workspace'],
      context: { telegram_chat_id: '6679471412' },
    }, {
      onSuccess: (result) => setPreview(result),
    });
  };

  const handleDeploy = () => {
    if (!preview?.workflow_json) return;
    deploy.mutate({
      name: preview.workflow_json.name as string || 'Automation',
      description: preview.human_summary,
      user_prompt: input,
      recipe_id: preview.recipe_id || undefined,
      workflow_json: preview.workflow_json,
      variables: preview.variables_used,
      trigger_type: preview.trigger_type,
      trigger_config: preview.trigger_config,
      activate: true,
    }, {
      onSuccess: () => {
        setPreview(null);
        setInput('');
        onClose();
      },
    });
  };

  return (
    <div className="fixed inset-0 z-[600] flex items-center justify-center font-sans">
      <div className="absolute inset-0 bg-bg/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-[520px] max-w-[90vw] bg-bg-panel border border-border rounded-[10px] shadow-[0_8px_32px_rgba(0,0,0,0.5)] p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-accent" />
            <h2 className="text-[16px] font-[600] text-text">New automation</h2>
          </div>
          <button onClick={onClose} className="p-1 rounded-[3px] text-text-quaternary hover:text-text-tertiary cursor-pointer">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Input */}
        <div className="relative">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleGenerate(); } }}
            placeholder="Describe what you want automated... e.g. 'Send me a daily summary of my Shopify orders every morning at 8am'"
            rows={3}
            className="w-full px-3 py-2.5 rounded-[7px] bg-bg-tertiary border border-border text-[13px] text-text-secondary placeholder:text-text-quaternary hover:border-border-secondary focus:outline-none focus:border-accent/60 resize-none pr-12"
          />
          <button
            onClick={handleGenerate}
            disabled={!input.trim() || generate.isPending}
            className="absolute right-2 bottom-2 w-8 h-8 flex items-center justify-center rounded-[5px] bg-accent text-white hover:bg-accent-hover disabled:opacity-30 transition-colors cursor-pointer"
          >
            {generate.isPending
              ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
              : <Send className="w-3.5 h-3.5" />
            }
          </button>
        </div>

        {/* Preview */}
        {preview && (
          <div className="mt-4 animate-[fadeIn_150ms_ease-out]">
            {preview.clarification_needed ? (
              <div className="p-3 rounded-[7px] bg-yellow-500/10 border border-yellow-500/20">
                <p className="text-[13px] text-yellow-200">{preview.clarification_needed}</p>
              </div>
            ) : preview.success ? (
              <div className="p-3 rounded-[7px] bg-green-500/10 border border-green-500/20">
                <div className="flex items-center gap-2 mb-2">
                  <Check className="w-3.5 h-3.5 text-green-400" />
                  <span className="text-[13px] font-[510] text-green-300">Ready to deploy</span>
                </div>
                <p className="text-[12px] text-text-secondary mb-2">{preview.human_summary}</p>
                {preview.recipe_name && (
                  <p className="text-[11px] text-text-quaternary">Recipe: {preview.recipe_name}</p>
                )}
                {preview.validation_errors.length > 0 && (
                  <div className="mt-2">
                    {preview.validation_errors.map((e, i) => (
                      <p key={i} className="text-[11px] text-red-400">{e}</p>
                    ))}
                  </div>
                )}
                <div className="flex justify-end mt-3">
                  <button
                    onClick={handleDeploy}
                    disabled={deploy.isPending}
                    className="px-4 py-1.5 rounded-[5px] text-[12px] font-[510] bg-accent text-white hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-40"
                  >
                    {deploy.isPending ? 'Deploying...' : 'Activate'}
                  </button>
                </div>
              </div>
            ) : (
              <div className="p-3 rounded-[7px] bg-red-500/10 border border-red-500/20">
                <p className="text-[13px] text-red-300">{preview.human_summary || 'Generation failed'}</p>
              </div>
            )}
          </div>
        )}

        <p className="text-[10px] text-text-quaternary mt-3">
          Powered by n8n · 400+ integrations · Runs on your Mac Mini
        </p>
      </div>
      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(-4px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}

// ── Main page ──

export default function AutomationsPage() {
  const { data, isLoading } = useAutomations();
  const { data: n8nData } = useN8nAutomations();
  const { data: healthData } = useAutomationsHealth();
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const activate = useActivateAutomation();
  const deactivate = useDeactivateAutomation();

  const toggleMutation = useMutation({
    mutationFn: async ({ id, enabled }: { id: string; enabled: boolean }) => {
      await fetch(`/api/automations/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['automations'] }),
  });

  const runMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`/api/automations/${id}/run`, { method: 'POST' });
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['automations'] }),
  });

  const handleToggle = useCallback((id: string, enabled: boolean) => {
    toggleMutation.mutate({ id, enabled });
  }, [toggleMutation]);

  const handleRun = useCallback((id: string) => {
    runMutation.mutate(id);
  }, [runMutation]);

  const handleSelect = useCallback((a: Automation) => {
    setSelectedId((prev) => prev === a.id ? null : a.id);
  }, []);

  const userAutomations = data?.user ?? [];
  const systemAutomations = data?.system ?? [];
  const n8nAutomations = n8nData?.automations ?? [];
  const n8nHealthy = healthData?.status === 'ok';

  return (
    <div className="bg-bg h-full overflow-y-auto font-sans">
      {/* New pill — fixed top-right */}
      <div className="fixed top-3 right-3 z-[300] flex items-center gap-2">
        {/* n8n health indicator */}
        {healthData && (
          <div className="flex items-center gap-1.5 h-8 px-2.5 rounded-full bg-[rgba(30,26,22,0.60)] backdrop-blur-[12px] border border-[rgba(255,245,235,0.06)]">
            <StatusDot color={n8nHealthy ? 'green' : 'gray'} size="sm" />
            <span className="text-[10px] text-text-quaternary">
              {n8nHealthy ? `${healthData.active_workflows ?? 0} active` : 'n8n offline'}
            </span>
          </div>
        )}
        <button
          onClick={() => setShowCreate(true)}
          className="
            flex items-center gap-1.5 h-8 px-3
            rounded-full
            bg-[rgba(30,26,22,0.60)] backdrop-blur-[12px]
            border border-[rgba(255,245,235,0.06)]
            shadow-[0_2px_12px_rgba(0,0,0,0.3)]
            text-text-secondary hover:text-text
            transition-all duration-150 cursor-pointer
            text-[12px] font-[510]
          "
        >
          <Sparkles className="w-3.5 h-3.5" /> New
        </button>
      </div>

      <div className="max-w-[640px] mx-auto px-5 md:px-8 pt-14 pb-10">

        {/* n8n-powered automations */}
        {n8nAutomations.length > 0 && (
          <div className="mb-6">
            <span className="text-[11px] font-[590] text-text-quaternary uppercase tracking-[0.06em] block mb-2">
              Automations
            </span>
            {n8nAutomations.map((a) => (
              <N8nAutomationCard
                key={a.id}
                automation={a}
                onActivate={() => activate.mutate(a.id)}
                onDeactivate={() => deactivate.mutate(a.id)}
              />
            ))}
          </div>
        )}

        {/* User automations (legacy YAML-based) */}
        {userAutomations.length > 0 && (
          <div className="mb-6">
            <span className="text-[11px] font-[590] text-text-quaternary uppercase tracking-[0.06em] block mb-2">
              Agent automations
            </span>
            {userAutomations.map((a) => (
              <div key={a.id}>
                <AutomationRow
                  automation={a}
                  onToggle={() => handleToggle(a.id, !a.enabled)}
                  onRun={() => handleRun(a.id)}
                  onClick={() => handleSelect(a)}
                />
                {selectedId === a.id && (
                  <DetailPanel
                    automation={a}
                    onClose={() => handleSelect(a)}
                    onRun={() => handleRun(a.id)}
                    onToggle={() => handleToggle(a.id, !a.enabled)}
                  />
                )}
              </div>
            ))}
          </div>
        )}

        {/* Empty state */}
        {n8nAutomations.length === 0 && userAutomations.length === 0 && !isLoading && (
          <div className="py-12">
            <p className="text-[13px] text-text-quaternary mb-4">
              No automations yet. Describe what you want automated and it'll run on your Mac Mini 24/7.
            </p>
            <button
              onClick={() => setShowCreate(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-[7px] text-[12px] font-[510] bg-accent text-white hover:bg-accent-hover transition-colors cursor-pointer"
            >
              <Sparkles className="w-3.5 h-3.5" /> Create your first automation
            </button>
          </div>
        )}

        {/* System automations */}
        {isLoading ? (
          <div className="mt-8 text-center">
            <span className="text-[12px] text-text-quaternary">Loading automations...</span>
          </div>
        ) : (
          <SystemGroup
            automations={systemAutomations}
            onToggle={handleToggle}
            onRun={handleRun}
            onSelect={handleSelect}
            selectedId={selectedId}
          />
        )}
      </div>

      {/* Create flow — natural language generation */}
      {showCreate && <GenerateFlow onClose={() => setShowCreate(false)} />}
    </div>
  );
}
