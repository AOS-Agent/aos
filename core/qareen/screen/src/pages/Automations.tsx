import { useState, useCallback, useMemo } from 'react';
import { useRegisterPageActions, type PageAction } from '@/hooks/usePageActions';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Zap, Play, ChevronDown, ChevronRight,
  X, Check, Clock, AlertCircle, Trash2,
  Pause, Send, Loader2, Sparkles, ArrowRight,
  MessageCircle, Mail, Sheet, Rss, Webhook,
  Calendar, Globe, History, Archive, Copy,
} from 'lucide-react';
import { Tag, StatusDot, type StatusDotColor } from '@/components/primitives';
import {
  useN8nAutomations, useAutomationsHealth,
  useGenerateAutomation, useDeployAutomation,
  useActivateAutomation, useDeactivateAutomation,
  useAutomationLifecycle,
  useAutomationContext, useExecutionHistory,
  useAutomationSuggestions,
  type N8nAutomation, type GenerateResult,
  type AutomationSuggestion, type LifecycleAction,
} from '@/hooks/useAutomations';
import { useConnectors } from '@/hooks/useConnectors';

// ---------------------------------------------------------------------------
// Automations — n8n-powered workflows + system crons.
// ---------------------------------------------------------------------------

interface SystemCron {
  id: string;
  name: string;
  description: string;
  frequency: string;
  at?: string;
  weekday?: string;
  every?: string;
  enabled: boolean;
  type: 'system';
  tier?: number;
  tier_label?: string;
  schedule_human: string;
  last_run?: string;
  last_run_ago?: string;
  last_status?: string | number;
  duration_ms?: number;
}

function useSystemCrons() {
  return useQuery({
    queryKey: ['automations'],
    queryFn: async (): Promise<{ system: SystemCron[]; total: number }> => {
      const res = await fetch('/api/automations');
      if (!res.ok) throw new Error('Failed');
      return res.json();
    },
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

// ── Status helpers ──

function statusColor(status: string | number | undefined): StatusDotColor {
  if (status === undefined || status === null) return 'gray';
  if (status === 'success' || status === 0) return 'green';
  if (status === 'running') return 'blue';
  return 'red';
}

// ── System cron row ──

function CronRow({
  cron,
  onRun,
  onClick,
}: {
  cron: SystemCron;
  onRun: () => void;
  onClick: () => void;
}) {
  const sc = statusColor(cron.last_status);

  return (
    <div
      onClick={onClick}
      className="flex items-center gap-3 py-3 px-1 cursor-pointer hover:bg-hover rounded-[5px] transition-colors duration-100 group"
    >
      <StatusDot color={cron.enabled ? sc : 'gray'} size="md" />

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-[510] text-text-secondary">
            {cron.name}
          </span>
          {!cron.enabled && (
            <Tag label="Paused" color="gray" size="sm" />
          )}
        </div>
        <span className="text-[11px] text-text-quaternary block mt-0.5 truncate">
          {cron.schedule_human}
          {cron.last_run_ago && ` · ${cron.last_run_ago}`}
        </span>
      </div>

      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-100">
        <button
          onClick={(e) => { e.stopPropagation(); onRun(); }}
          title="Run now"
          className="w-7 h-7 flex items-center justify-center rounded-[5px] text-text-quaternary hover:text-accent hover:bg-bg-tertiary transition-colors cursor-pointer"
        >
          <Play className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}

// ── System automations group (collapsible) ──

function SystemGroup({
  crons,
  onRun,
}: {
  crons: SystemCron[];
  onRun: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
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
        <span className="text-[10px] text-text-quaternary">{crons.length}</span>
      </button>
      {expanded && (
        <div>
          {crons.map((c) => (
            <CronRow
              key={c.id}
              cron={c}
              onRun={() => onRun(c.id)}
              onClick={() => setSelectedId(prev => prev === c.id ? null : c.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Suggestion card ──

const CONNECTOR_ICONS: Record<string, typeof Zap> = {
  mail: Mail, send: Send, github: Globe, 'book-open': Zap,
  calendar: Calendar, users: Globe, 'message-circle': MessageCircle,
  'sticky-note': Zap, 'check-square': Check,
};

function SuggestionCard({
  suggestion,
  onSetUp,
  connectorStatuses,
}: {
  suggestion: AutomationSuggestion;
  onSetUp: (description: string) => void;
  connectorStatuses: Record<string, string>;
}) {
  const ConnectorIcon = CONNECTOR_ICONS[suggestion.source_icon] || Zap;
  const allConnected = suggestion.required_connectors.every(
    id => connectorStatuses[id] === 'connected'
  );

  return (
    <div
      className="rounded-[10px] border border-border p-4 group transition-all duration-150 hover:border-border-secondary cursor-pointer"
      style={{ background: 'var(--glass-bg)', backdropFilter: 'blur(12px)' }}
      onClick={() => onSetUp(suggestion.description)}
    >
      <div className="flex items-start gap-3">
        <div
          className="w-8 h-8 rounded-[7px] flex items-center justify-center flex-shrink-0 mt-0.5"
          style={{ backgroundColor: suggestion.source_color + '18' }}
        >
          <ConnectorIcon className="w-4 h-4" style={{ color: suggestion.source_color }} />
        </div>
        <div className="flex-1 min-w-0">
          <span className="text-[13px] font-[560] text-text block">{suggestion.name}</span>
          <p className="text-[11px] text-text-quaternary mt-1 leading-relaxed line-clamp-2">
            {suggestion.description}
          </p>
        </div>
      </div>
      <div className="flex items-center justify-between mt-3 pt-2.5 border-t border-border">
        <div className="flex items-center gap-1.5">
          {suggestion.required_connectors.map(id => {
            const status = connectorStatuses[id];
            const color = status === 'connected' ? '#30D158' : status === 'partial' ? '#FFD60A' : '#6B6560';
            return (
              <div key={id} className="flex items-center gap-1">
                <div className="w-[5px] h-[5px] rounded-full" style={{ background: color }} />
                <span className="text-[9px] text-text-quaternary capitalize">{id.replace(/-/g, ' ')}</span>
              </div>
            );
          })}
        </div>
        <span className={`text-[11px] font-[510] transition-opacity ${
          allConnected
            ? 'text-green-400 opacity-100'
            : 'text-accent opacity-0 group-hover:opacity-100'
        }`}>
          {allConnected ? 'Ready' : 'Set up'}
        </span>
      </div>
    </div>
  );
}

function SuggestionGrid({
  suggestions,
  onSetUp,
  connectorStatuses,
}: {
  suggestions: AutomationSuggestion[];
  onSetUp: (description: string) => void;
  connectorStatuses: Record<string, string>;
}) {
  if (suggestions.length === 0) return null;

  return (
    <div className="mb-8">
      <span className="text-[11px] font-[590] text-text-quaternary uppercase tracking-[0.06em] block mb-3">
        Suggested for you
      </span>
      <div className="grid grid-cols-2 gap-3">
        {suggestions.slice(0, 6).map((s) => (
          <SuggestionCard key={s.id} suggestion={s} onSetUp={onSetUp} connectorStatuses={connectorStatuses} />
        ))}
      </div>
      {suggestions.length > 6 && (
        <p className="text-[10px] text-text-quaternary mt-2 text-center">
          {suggestions.length - 6} more suggestions available
        </p>
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
  email_digest: { trigger: Clock, action: Mail, triggerLabel: 'Schedule', actionLabel: 'Gmail' },
  calendar_to_telegram: { trigger: Calendar, action: MessageCircle, triggerLabel: 'Calendar', actionLabel: 'Telegram' },
  shopify_orders: { trigger: Clock, action: MessageCircle, triggerLabel: 'Shopify', actionLabel: 'Telegram' },
};

function cronToHuman(cron: string): string {
  if (!cron) return '';
  const parts = cron.split(' ');
  if (parts.length < 5) return cron;
  const [min, hour, , , dow] = parts;
  const h = parseInt(hour);
  const m = parseInt(min);

  // Handle non-numeric cron fields (*/30, *, ranges, etc.)
  if (isNaN(h) || isNaN(m)) {
    if (min.startsWith('*/')) return `Every ${min.slice(2)} minutes`;
    if (hour.startsWith('*/')) return `Every ${hour.slice(2)} hours`;
    if (hour === '*' && !isNaN(m)) return m === 0 ? 'Every hour' : `Every hour at :${m.toString().padStart(2, '0')}`;
    if (min === '*' && !isNaN(h)) return `Continuously at ${h > 12 ? h - 12 : h || 12}${h < 12 ? 'AM' : 'PM'}`;
    return cron;
  }

  const suffix = h < 12 ? 'AM' : 'PM';
  const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
  const time = `${h12}:${m.toString().padStart(2, '0')} ${suffix}`;

  if (dow === '*') return `Daily at ${time}`;
  if (dow === '1-5') return `Weekdays at ${time}`;
  if (dow === '0') return `Sundays at ${time}`;
  if (dow === '1') return `Mondays at ${time}`;
  return `${time}`;
}

// ── Node execution result type ──

interface NodeResult {
  node: string;
  status: 'success' | 'error';
  duration_ms: number;
  items: number;
  error: string | null;
  simulated?: boolean;
}

// ── Run result panel — shows per-node status after a run ──

function RunResultPanel({ results, overallStatus, error }: {
  results: NodeResult[];
  overallStatus: string;
  error: string | null;
}) {
  return (
    <div className="space-y-1.5">
      {results.map((r, i) => (
        <div key={i} className="flex items-center gap-2.5 py-1.5 px-2 rounded-[5px] bg-bg-tertiary">
          <div className={`w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0 ${
            r.status === 'success' ? 'bg-green-500/20' : 'bg-red-500/20'
          }`}>
            {r.status === 'success'
              ? <Check className="w-3 h-3 text-green-400" />
              : <AlertCircle className="w-3 h-3 text-red-400" />
            }
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-[12px] font-[510] text-text-secondary">{r.node}</span>
              {r.simulated && <span className="text-[9px] text-text-quaternary italic">simulated</span>}
            </div>
            {r.error && (
              <p className="text-[10px] text-red-400 mt-0.5 truncate">{r.error}</p>
            )}
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {r.items > 0 && (
              <span className="text-[10px] text-text-quaternary">{r.items} item{r.items !== 1 ? 's' : ''}</span>
            )}
            <span className="text-[10px] text-text-quaternary">{r.duration_ms}ms</span>
          </div>
        </div>
      ))}
      {error && overallStatus === 'error' && (
        <div className="mt-1.5 p-2 rounded-[5px] bg-red-500/10 border border-red-500/15">
          <p className="text-[11px] text-red-400">{error}</p>
        </div>
      )}
    </div>
  );
}

// ── n8n Automation card ──

function ExecutionHistoryPanel({ automationId }: { automationId: string }) {
  const { data, isLoading } = useExecutionHistory(automationId);
  const executions = data?.executions ?? [];

  if (isLoading) {
    return (
      <div className="py-3 text-center">
        <Loader2 className="w-3.5 h-3.5 animate-spin text-text-quaternary inline-block" />
      </div>
    );
  }

  if (executions.length === 0) {
    return (
      <div className="py-3 text-center">
        <span className="text-[11px] text-text-quaternary">No executions yet</span>
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      {executions.slice(0, 10).map((exec: Record<string, unknown>) => {
        const status = exec.status as string || 'unknown';
        const finished = exec.stoppedAt as string || exec.startedAt as string;
        const sc: StatusDotColor = status === 'success' ? 'green' : status === 'error' ? 'red' : status === 'running' ? 'blue' : 'gray';
        const duration = exec.stoppedAt && exec.startedAt
          ? Math.round((new Date(exec.stoppedAt as string).getTime() - new Date(exec.startedAt as string).getTime()) / 1000)
          : null;

        return (
          <div key={exec.id as string} className="flex items-center gap-2.5 py-1.5 px-2 rounded-[5px] hover:bg-hover transition-colors">
            <StatusDot color={sc} size="sm" />
            <span className="text-[11px] text-text-secondary flex-1 capitalize">{status}</span>
            {duration !== null && (
              <span className="text-[10px] text-text-quaternary">{duration}s</span>
            )}
            {finished && (
              <span className="text-[10px] text-text-quaternary">
                {new Date(finished).toLocaleString(undefined, {
                  month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
                })}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function WebhookUrlBadge({ automationId }: { automationId: string }) {
  const { data } = useQuery({
    queryKey: ['webhook-url', automationId],
    queryFn: async () => {
      const res = await fetch(`/api/automations/${automationId}/webhook-url`);
      return res.json();
    },
    staleTime: 60_000,
  });
  const [copied, setCopied] = useState(false);

  if (!data?.webhook_url) return null;

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(data.webhook_url);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div
      className="flex items-center gap-2 mb-3 px-2.5 py-1.5 rounded-[7px] bg-bg-tertiary border border-border"
      onClick={(e) => e.stopPropagation()}
    >
      <Webhook className="w-3.5 h-3.5 text-accent shrink-0" />
      <span className="text-[11px] text-text-tertiary truncate flex-1 font-mono select-all">{data.webhook_url}</span>
      <span className="text-[9px] text-text-quaternary uppercase shrink-0">{data.method}</span>
      <button
        onClick={handleCopy}
        className="shrink-0 w-5 h-5 flex items-center justify-center rounded-[3px] text-text-quaternary hover:text-accent transition-colors cursor-pointer"
      >
        {copied ? <Check className="w-3 h-3 text-green-400" /> : <Copy className="w-3 h-3" />}
      </button>
    </div>
  );
}

function N8nAutomationCard({
  automation,
  onView,
}: {
  automation: N8nAutomation;
  onView: () => void;
}) {
  const [showHistory, setShowHistory] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [runResult, setRunResult] = useState<{ status: string; node_results: NodeResult[]; error: string | null } | null>(null);
  const qc = useQueryClient();
  const lifecycle = useAutomationLifecycle();

  const handleRunNow = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsRunning(true);
    setRunResult(null);
    try {
      const res = await fetch(`/api/automations/${automation.id}/run`, { method: 'POST' });
      const data = await res.json();
      setRunResult(data);
      qc.invalidateQueries({ queryKey: ['n8n-automations'] });
    } catch {
      setRunResult({ status: 'error', node_results: [], error: 'Request failed' });
    }
    setIsRunning(false);
  };
  const statusColors: Record<string, StatusDotColor> = {
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
      className="rounded-[10px] border border-border p-4 mb-3 group transition-all duration-150 hover:border-border-secondary cursor-pointer"
      style={{ background: 'var(--glass-bg)', backdropFilter: 'blur(12px)' }}
      onClick={onView}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2.5">
          <StatusDot color={sc} size="md" pulse={automation.status === 'active'} />
          <span className="text-[14px] font-[560] text-text">{automation.name}</span>
          <Tag label={automation.status} color={sc as any} size="sm" />
        </div>
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={(e) => { e.stopPropagation(); handleRunNow(e); }}
            disabled={isRunning}
            className="flex items-center gap-1 px-2 py-1 rounded-[5px] text-[11px] font-[510] text-text-quaternary hover:text-accent hover:bg-bg-tertiary transition-colors cursor-pointer disabled:opacity-40"
          >
            {isRunning ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
            {isRunning ? 'Running...' : 'Run now'}
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); setShowHistory(!showHistory); }}
            className={`flex items-center gap-1 px-2 py-1 rounded-[5px] text-[11px] font-[510] transition-colors cursor-pointer ${
              showHistory
                ? 'text-accent bg-accent/10'
                : 'text-text-quaternary hover:text-text-tertiary hover:bg-bg-tertiary'
            }`}
          >
            <History className="w-3 h-3" /> Runs
          </button>
          {automation.status === 'active' ? (
            <button
              onClick={(e) => { e.stopPropagation(); lifecycle.mutate({ id: automation.id, action: 'pause' }); }}
              className="flex items-center gap-1 px-2 py-1 rounded-[5px] text-[11px] font-[510] text-text-quaternary hover:text-yellow-400 hover:bg-bg-tertiary transition-colors cursor-pointer"
            >
              <Pause className="w-3 h-3" /> Pause
            </button>
          ) : automation.status !== 'archived' ? (
            <button
              onClick={(e) => { e.stopPropagation(); lifecycle.mutate({ id: automation.id, action: 'activate' }); }}
              className="flex items-center gap-1 px-2 py-1 rounded-[5px] text-[11px] font-[510] text-text-quaternary hover:text-green-400 hover:bg-bg-tertiary transition-colors cursor-pointer"
            >
              <Play className="w-3 h-3" /> Activate
            </button>
          ) : null}
          <button
            onClick={(e) => { e.stopPropagation(); lifecycle.mutate({ id: automation.id, action: 'delete' }); }}
            className="flex items-center gap-1 px-2 py-1 rounded-[5px] text-[11px] font-[510] text-text-quaternary hover:text-red-400 hover:bg-red-500/10 transition-colors cursor-pointer"
          >
            <Trash2 className="w-3 h-3" />
          </button>
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

      {/* Webhook URL (only shown for webhook triggers) */}
      {automation.trigger_type === 'webhook' && (
        <WebhookUrlBadge automationId={automation.id} />
      )}

      {/* Description */}
      <p className="text-[12px] text-text-quaternary leading-relaxed line-clamp-2">
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
        <button
          onClick={(e) => { e.stopPropagation(); handleRunNow(e); }}
          disabled={isRunning}
          className="ml-auto flex items-center gap-1 px-2.5 py-1 rounded-[5px] text-[11px] font-[510] text-text-quaternary hover:text-accent hover:bg-bg-tertiary transition-colors cursor-pointer disabled:opacity-40"
        >
          {isRunning ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
          {isRunning ? 'Running...' : 'Run now'}
        </button>
      </div>

      {/* Run result — shows per-node status after clicking "Run now" */}
      {runResult && (
        <div className="mt-3 pt-2.5 border-t border-border animate-[fadeIn_150ms_ease-out]">
          <div className="flex items-center gap-2 mb-2">
            {runResult.status === 'success'
              ? <><Check className="w-3.5 h-3.5 text-green-400" /><span className="text-[12px] font-[510] text-green-300">All steps passed</span></>
              : runResult.status === 'error'
              ? <><AlertCircle className="w-3.5 h-3.5 text-red-400" /><span className="text-[12px] font-[510] text-red-300">Execution failed</span></>
              : <><Loader2 className="w-3.5 h-3.5 text-text-quaternary animate-spin" /><span className="text-[12px] font-[510] text-text-tertiary">{runResult.status}</span></>
            }
            <button
              onClick={(e) => { e.stopPropagation(); setRunResult(null); }}
              className="ml-auto w-5 h-5 flex items-center justify-center rounded-[3px] text-text-quaternary hover:text-text-tertiary cursor-pointer"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
          {runResult.node_results && runResult.node_results.length > 0 && (
            <RunResultPanel
              results={runResult.node_results}
              overallStatus={runResult.status}
              error={runResult.error}
            />
          )}
          {runResult.error && (!runResult.node_results || runResult.node_results.length === 0) && (
            <p className="text-[11px] text-red-400">{runResult.error}</p>
          )}
        </div>
      )}

      {/* Execution history (expandable) */}
      {showHistory && !runResult && (
        <div className="mt-3 pt-2.5 border-t border-border animate-[fadeIn_150ms_ease-out]">
          <span className="text-[11px] font-[510] text-text-quaternary block mb-2">Recent runs</span>
          <ExecutionHistoryPanel automationId={automation.id} />
        </div>
      )}
    </div>
  );
}

// ── Category labels ──

const CATEGORY_LABELS: Record<string, string> = {
  communication: 'Communication',
  productivity: 'Productivity',
  knowledge: 'Knowledge',
  development: 'Development',
  data: 'Data & Pipelines',
  ecommerce: 'Commerce',
  monitoring: 'Monitoring',
  general: 'General',
};

const CATEGORY_ORDER = ['communication', 'productivity', 'knowledge', 'development', 'data', 'ecommerce'];

type FilterTab = 'all' | 'active' | 'drafts' | 'system';

// ── Filter bar — glass pill group ──

function FilterBar({
  active,
  onChange,
  counts,
  onCreate,
}: {
  active: FilterTab;
  onChange: (tab: FilterTab) => void;
  counts: { all: number; active: number; drafts: number; system: number };
  onCreate: () => void;
}) {
  const tabs: { id: FilterTab; label: string; count?: number }[] = [
    { id: 'all', label: 'All' },
    { id: 'active', label: 'Active', count: counts.active },
    { id: 'drafts', label: 'Drafts', count: counts.drafts },
    { id: 'system', label: 'System', count: counts.system },
  ];

  return (
    <div className="fixed top-3 right-3 z-[300] flex items-center gap-2">
      <div
        className="inline-flex items-center gap-0.5 h-8 rounded-full px-1"
        style={{
          background: 'rgba(30, 26, 22, 0.75)',
          backdropFilter: 'blur(16px)',
          WebkitBackdropFilter: 'blur(16px)',
          border: '1px solid rgba(255, 245, 235, 0.10)',
          boxShadow: '0 2px 12px rgba(0, 0, 0, 0.3)',
        }}
      >
        {tabs.map((tab) => {
          const isActive = tab.id === active;
          return (
            <button
              key={tab.id}
              onClick={() => onChange(tab.id)}
              className="flex items-center gap-1 px-2.5 h-6 rounded-full text-[11px] font-[510] transition-colors cursor-pointer"
              style={{
                background: isActive ? 'rgba(255, 245, 235, 0.12)' : 'transparent',
                color: isActive ? 'var(--color-text)' : 'var(--color-text-tertiary)',
                transitionDuration: '150ms',
              }}
            >
              {tab.label}
              {tab.count != null && tab.count > 0 && (
                <span className="text-[9px] text-text-quaternary tabular-nums">{tab.count}</span>
              )}
            </button>
          );
        })}
      </div>

      <button
        onClick={onCreate}
        className="flex items-center gap-1.5 h-8 px-3 rounded-full text-[12px] font-[510] text-white cursor-pointer transition-colors"
        style={{ background: '#D9730D' }}
      >
        <Sparkles className="w-3.5 h-3.5" /> New
      </button>
    </div>
  );
}

// ── Featured suggestion cards (larger) ──

function FeaturedCard({
  suggestion,
  onSetUp,
  connectorStatuses,
}: {
  suggestion: AutomationSuggestion;
  onSetUp: () => void;
  connectorStatuses: Record<string, string>;
}) {
  const ConnectorIcon = CONNECTOR_ICONS[suggestion.source_icon] || Zap;
  const allConnected = suggestion.required_connectors.every(
    id => connectorStatuses[id] === 'connected'
  );

  return (
    <div
      className="rounded-[10px] border border-border p-5 group transition-all duration-150 hover:border-border-secondary cursor-pointer"
      style={{ background: 'var(--glass-bg)', backdropFilter: 'blur(12px)' }}
      onClick={onSetUp}
    >
      <div className="flex items-start gap-3">
        <div
          className="w-10 h-10 rounded-[9px] flex items-center justify-center flex-shrink-0"
          style={{ backgroundColor: suggestion.source_color + '18' }}
        >
          <ConnectorIcon className="w-5 h-5" style={{ color: suggestion.source_color }} />
        </div>
        <div className="flex-1 min-w-0">
          <span className="text-[14px] font-[560] text-text block">{suggestion.name}</span>
          <p className="text-[12px] text-text-tertiary mt-1 leading-relaxed line-clamp-2">
            {suggestion.description}
          </p>
          <div className="flex items-center gap-2 mt-2.5">
            <div className="flex items-center gap-1.5">
              {suggestion.required_connectors.map(id => {
                const status = connectorStatuses[id];
                const color = status === 'connected' ? '#30D158' : status === 'partial' ? '#FFD60A' : '#6B6560';
                return (
                  <div key={id} className="flex items-center gap-1">
                    <div className="w-[5px] h-[5px] rounded-full" style={{ background: color }} />
                    <span className="text-[9px] text-text-quaternary capitalize">{id.replace(/-/g, ' ')}</span>
                  </div>
                );
              })}
            </div>
            <span className={`text-[11px] font-[510] ml-auto ${allConnected ? 'text-green-400' : 'text-accent'}`}>
              {allConnected ? 'Ready →' : 'Set up →'}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Category section ──

function CategorySection({
  category,
  suggestions,
  onSetUp,
  connectorStatuses,
}: {
  category: string;
  suggestions: AutomationSuggestion[];
  onSetUp: (desc: string) => void;
  connectorStatuses: Record<string, string>;
}) {
  if (suggestions.length === 0) return null;

  return (
    <div className="mb-6">
      <span className="text-[11px] font-[590] text-text-quaternary uppercase tracking-[0.06em] block mb-2.5">
        {CATEGORY_LABELS[category] || category}
      </span>
      <div className="grid grid-cols-2 gap-3">
        {suggestions.map((s) => (
          <SuggestionCard key={s.id} suggestion={s} onSetUp={onSetUp} connectorStatuses={connectorStatuses} />
        ))}
      </div>
    </div>
  );
}

// ── Main page ──

export default function AutomationsPage() {
  const { data: cronsData, isLoading: cronsLoading } = useSystemCrons();
  const { data: n8nData, isLoading: n8nLoading } = useN8nAutomations();
  const { data: healthData } = useAutomationsHealth();
  const { data: suggestionsData, isLoading: suggestionsLoading } = useAutomationSuggestions();
  const { connectors: connectorList } = useConnectors();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<FilterTab>('all');

  // Build connector status lookup: id → status
  const connectorStatuses = useMemo(() => {
    const map: Record<string, string> = {};
    for (const c of connectorList) map[c.id] = c.status;
    return map;
  }, [connectorList]);
  // Lifecycle actions handled inside N8nAutomationCard via useAutomationLifecycle

  const runMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`/api/automations/${id}/run`, { method: 'POST' });
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['n8n-automations'] }),
  });

  const handleRun = useCallback((id: string) => {
    runMutation.mutate(id);
  }, [runMutation]);

  const pageActions: PageAction[] = useMemo(() => [
    {
      id: 'automations.create',
      label: 'Create new automation',
      category: 'create',
      execute: () => navigate('/automations/new'),
    },
  ], [navigate]);
  useRegisterPageActions(pageActions);

  const systemCrons = cronsData?.system ?? [];
  const n8nAutomations = n8nData?.automations ?? [];
  const suggestions = suggestionsData?.suggestions ?? [];
  const isReady = !cronsLoading && !n8nLoading && !suggestionsLoading;

  // Derive counts
  const activeAutomations = n8nAutomations.filter(a => a.status === 'active');
  const draftAutomations = n8nAutomations.filter(a => a.status === 'draft' || a.status === 'paused');
  const counts = {
    all: n8nAutomations.length + systemCrons.length,
    active: activeAutomations.length,
    drafts: draftAutomations.length,
    system: systemCrons.length,
  };

  // Group suggestions by category
  const featured = suggestions.slice(0, 3);
  const byCategory = useMemo(() => {
    const groups: Record<string, AutomationSuggestion[]> = {};
    for (const s of suggestions.slice(3)) {
      const cat = s.category || 'general';
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(s);
    }
    return groups;
  }, [suggestions]);

  // Loading state
  if (!isReady) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-center">
          <Loader2 className="w-5 h-5 animate-spin text-text-quaternary mx-auto mb-2" />
          <span className="text-[12px] text-text-quaternary">Loading automations...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto font-sans">
      <FilterBar
        active={filter}
        onChange={setFilter}
        counts={counts}
        onCreate={() => navigate('/automations/new')}
      />

      <div className="max-w-[640px] mx-auto px-5 md:px-8 pt-14 pb-10">

        {/* Active automations — shown in All or Active filter */}
        {(filter === 'all' || filter === 'active') && activeAutomations.length > 0 && (
          <div className="mb-6">
            <span className="text-[11px] font-[590] text-text-quaternary uppercase tracking-[0.06em] block mb-2">
              Active
            </span>
            {activeAutomations.map((a) => (
              <N8nAutomationCard
                key={a.id}
                automation={a}
                onView={() => navigate(`/automations/${a.id}`)}
              />
            ))}
          </div>
        )}

        {/* Draft/paused automations — shown in All or Drafts filter */}
        {(filter === 'all' || filter === 'drafts') && draftAutomations.length > 0 && (
          <div className="mb-6">
            <span className="text-[11px] font-[590] text-text-quaternary uppercase tracking-[0.06em] block mb-2">
              Drafts
            </span>
            {draftAutomations.map((a) => (
              <N8nAutomationCard
                key={a.id}
                automation={a}
                onView={() => navigate(`/automations/${a.id}`)}
              />
            ))}
          </div>
        )}

        {/* Featured suggestions — shown in All filter when there are suggestions */}
        {filter === 'all' && featured.length > 0 && (
          <div className="mb-6">
            <span className="text-[11px] font-[590] text-text-quaternary uppercase tracking-[0.06em] block mb-2.5">
              Featured
            </span>
            <div className="space-y-3">
              {featured.map((s) => (
                <FeaturedCard
                  key={s.id}
                  suggestion={s}
                  onSetUp={() => navigate('/automations/new')}
                  connectorStatuses={connectorStatuses}
                />
              ))}
            </div>
          </div>
        )}

        {/* Category sections — shown in All filter */}
        {filter === 'all' && CATEGORY_ORDER.map(cat => (
          <CategorySection
            key={cat}
            category={cat}
            suggestions={byCategory[cat] || []}
            onSetUp={() => navigate('/automations/new')}
            connectorStatuses={connectorStatuses}
          />
        ))}

        {/* System crons — shown in All or System filter */}
        {(filter === 'all' || filter === 'system') && (
          <SystemGroup crons={systemCrons} onRun={handleRun} />
        )}

        {/* Empty state — only when truly empty */}
        {n8nAutomations.length === 0 && suggestions.length === 0 && systemCrons.length === 0 && (
          <div className="py-16 text-center">
            <Zap className="w-8 h-8 text-text-quaternary mx-auto mb-3 opacity-30" />
            <p className="text-[13px] text-text-quaternary mb-4">
              No automations yet. Describe what you want automated and it'll run on your Mac 24/7.
            </p>
            <button
              onClick={() => navigate('/automations/new')}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-[7px] text-[12px] font-[510] bg-accent text-white hover:bg-accent-hover transition-colors cursor-pointer"
            >
              <Sparkles className="w-3.5 h-3.5" /> Create your first automation
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
