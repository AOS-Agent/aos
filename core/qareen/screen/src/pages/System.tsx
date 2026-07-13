import { useState, useCallback } from 'react';
import {
  Activity, Terminal, HardDrive, Cpu, ChevronDown, ChevronRight,
  Play, RotateCcw, FileText, AlertTriangle, XCircle, Clock,
  CheckCircle2, Loader2, RefreshCw, Trash2,
  MemoryStick, FolderOpen, Lightbulb, Radio,
} from 'lucide-react';
import { useServices } from '@/hooks/useServices';
import { useCrons, type CronJob } from '@/hooks/useCrons';
import { useHealth } from '@/hooks/useHealth';
import { useAttention, type AttentionItem } from '@/hooks/useAttention';
import { useResources, useRefreshResources } from '@/hooks/useResources';
import { useRestartService, useRunCron, useToggleCron, fetchCronOutput, fetchServiceLogs } from '@/hooks/useSystemActions';
import { SkeletonRows, SkeletonCards } from '@/components/primitives/Skeleton';
import { StatusDot } from '@/components/primitives/StatusDot';

const TIER_LABELS: Record<number, string> = { 1: 'System Health', 2: 'Knowledge & Awareness', 3: 'Integration', 4: 'Intelligence' };
const TIER_ORDER = [1, 2, 3, 4, 0];
const SERVICE_META: Record<string, { description: string; onDemand?: boolean }> = {
  listen: { description: 'Voice transcription pipeline' },
  bridge: { description: 'Telegram + Slack messaging' },
  memory: { description: 'MCP context server', onDemand: true },
};

function timeAgo(iso: string | null): string {
  if (!iso) return '\u2014';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function cronStatusColor(status: string): 'green' | 'red' | 'yellow' | 'gray' {
  if (status === 'ok') return 'green';
  if (status === 'failed') return 'red';
  if (status === 'stale') return 'yellow';
  return 'gray';
}

interface CronJobExt extends CronJob { tier?: number; description?: string; }

function barColor(pct: number) { return pct > 85 ? 'bg-red' : pct > 70 ? 'bg-yellow' : 'bg-green'; }
function textColor(pct: number) { return pct > 85 ? 'text-red' : pct > 70 ? 'text-yellow' : 'text-text-tertiary'; }

function GaugeBar({ pct, className = '' }: { pct: number; className?: string }) {
  return (
    <div className={`h-[5px] bg-bg-tertiary rounded-full overflow-hidden ${className}`}>
      <div
        className={`h-full rounded-full transition-all duration-500 ${barColor(pct)}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

/* ---------- Verdict Banner ---------- */
function VerdictBanner() {
  const { data, isLoading } = useAttention();
  if (isLoading) return (
    <div className="rounded-[7px] p-5 mb-8 animate-pulse bg-bg-secondary">
      <div className="h-5 w-48 bg-bg-tertiary rounded" />
      <div className="h-3 w-64 bg-bg-tertiary rounded mt-2.5" />
    </div>
  );
  if (!data) return null;

  const ringColor = data.verdict === 'healthy' ? 'border-green/40' : data.verdict === 'warning' ? 'border-yellow/40' : 'border-red/40';
  const dotColor = data.verdict === 'healthy' ? 'bg-green' : data.verdict === 'warning' ? 'bg-yellow' : 'bg-red';
  const bg = data.verdict === 'healthy' ? 'bg-green-muted' : data.verdict === 'warning' ? 'bg-yellow-muted' : 'bg-red-muted';

  return (
    <div className={`${bg} rounded-[7px] p-5 mb-8 border ${ringColor}`}>
      <div className="flex items-center gap-3">
        <span className="relative flex items-center justify-center w-5 h-5 shrink-0">
          <span className={`w-2.5 h-2.5 rounded-full ${dotColor} ${data.verdict !== 'healthy' ? 'animate-pulse' : ''}`} />
        </span>
        <div>
          <span className="text-[15px] font-[600] text-text tracking-[-0.01em]">{data.verdict_text}</span>
          <p className="text-[11px] text-text-tertiary mt-0.5">{data.summary}</p>
        </div>
      </div>
    </div>
  );
}

/* ---------- Attention Panel ---------- */
function AttentionPanel() {
  const { data } = useAttention();
  const restartService = useRestartService();
  const runCron = useRunCron();
  if (!data || data.items.length === 0) return null;

  const iconFor = (item: AttentionItem) => {
    if (item.icon === 'alert-triangle') return <AlertTriangle className="w-3.5 h-3.5" />;
    if (item.icon === 'x-circle') return <XCircle className="w-3.5 h-3.5" />;
    if (item.icon === 'clock') return <Clock className="w-3.5 h-3.5" />;
    return <AlertTriangle className="w-3.5 h-3.5" />;
  };

  return (
    <div className="mb-10 space-y-1.5">
      {data.items.map((item, i) => (
        <div
          key={i}
          className="flex items-center gap-3 px-4 py-3 rounded-[7px] bg-bg-secondary border border-border hover:bg-bg-tertiary transition-colors cursor-default"
          style={{ transitionDuration: 'var(--duration-instant)' }}
        >
          <span className={item.type === 'error' ? 'text-red' : 'text-yellow'}>{iconFor(item)}</span>
          <div className="flex-1 min-w-0">
            <span className="text-[13px] font-[510] text-text">{item.text}</span>
            {item.detail && <span className="text-[11px] text-text-quaternary ml-2">{item.detail}</span>}
          </div>
          {item.action_type === 'restart_service' && item.action_target && (
            <button
              onClick={() => restartService.mutate(item.action_target!)}
              disabled={restartService.isPending}
              className="text-[10px] font-[590] text-accent px-2.5 py-1 rounded-[5px] bg-bg-tertiary hover:bg-bg-quaternary transition-colors disabled:opacity-40 cursor-pointer"
            >
              {restartService.isPending ? 'Restarting...' : 'Restart'}
            </button>
          )}
          {item.action_type === 'run_cron' && item.action_target && (
            <button
              onClick={() => runCron.mutate(item.action_target!)}
              disabled={runCron.isPending}
              className="text-[10px] font-[590] text-accent px-2.5 py-1 rounded-[5px] bg-bg-tertiary hover:bg-bg-quaternary transition-colors disabled:opacity-40 cursor-pointer"
            >
              {runCron.isPending ? 'Running...' : 'Run now'}
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

/* ---------- Resources Panel ---------- */
function ResourcesPanel() {
  const { data: health, isLoading: healthLoading } = useHealth();
  const [expanded, setExpanded] = useState(false);
  const { data: resources, isLoading: resourcesLoading } = useResources(expanded);
  const refreshResources = useRefreshResources();
  const [refreshing, setRefreshing] = useState(false);
  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try { await refreshResources(); } catch { /* empty */ }
    setRefreshing(false);
  }, [refreshResources]);

  if (healthLoading || !health) return (
    <div className="flex gap-8 mb-10 animate-pulse">
      <div className="h-4 w-40 bg-bg-tertiary rounded" />
      <div className="h-4 w-40 bg-bg-tertiary rounded" />
    </div>
  );

  return (
    <div className="mb-10">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-6 py-3 px-4 -mx-1 rounded-[7px] hover:bg-hover transition-colors cursor-pointer group"
        style={{ transitionDuration: 'var(--duration-instant)' }}
      >
        {/* Disk gauge */}
        <div className="flex items-center gap-3">
          <HardDrive className="w-3.5 h-3.5 text-text-quaternary" />
          <span className="text-[11px] text-text-quaternary">Disk</span>
          <GaugeBar pct={health.disk_pct} className="w-24" />
          <span className={`text-[10px] font-mono ${textColor(health.disk_pct)}`}>{health.disk_pct}%</span>
          <span className="text-[10px] font-mono text-text-quaternary hidden sm:inline">{health.disk_free_gb}GB free</span>
        </div>

        {/* RAM gauge */}
        <div className="flex items-center gap-3">
          <Cpu className="w-3.5 h-3.5 text-text-quaternary" />
          <span className="text-[11px] text-text-quaternary">RAM</span>
          <GaugeBar pct={health.ram_pct} className="w-24" />
          <span className={`text-[10px] font-mono ${textColor(health.ram_pct)}`}>{health.ram_pct}%</span>
          <span className="text-[10px] font-mono text-text-quaternary hidden sm:inline">{health.ram_used_gb}GB</span>
        </div>

        <span className="ml-auto text-text-quaternary opacity-0 group-hover:opacity-100 transition-opacity">
          {expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </span>
      </button>

      {expanded && (
        <div className="mt-3 bg-bg-secondary rounded-[7px] p-5 border border-border space-y-6">
          {resourcesLoading ? (
            <div className="space-y-3"><div className="h-24 bg-bg-tertiary rounded animate-pulse" /></div>
          ) : resources ? (
            <>
              {/* Drives */}
              <div>
                <div className="flex items-center gap-1.5 mb-3">
                  <HardDrive className="w-3 h-3 text-text-quaternary" />
                  <span className="type-overline text-text-quaternary">Drives</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {resources.drives.map(d => (
                    <div key={d.mount} className="bg-bg-tertiary rounded-[7px] p-4">
                      <div className="flex items-center justify-between mb-3">
                        <span className="text-[13px] font-[510] text-text-secondary">{d.label}</span>
                        <span className={`text-[10px] font-mono ${textColor(d.pct)}`}>{d.pct}%</span>
                      </div>
                      <div className="w-full h-2 bg-bg-quaternary rounded-full overflow-hidden mb-2">
                        <div className={`h-full rounded-full transition-all duration-500 ${barColor(d.pct)}`} style={{ width: `${d.pct}%` }} />
                      </div>
                      <div className="flex justify-between text-[10px] font-mono text-text-quaternary">
                        <span>{d.used_gb}GB used</span>
                        <span>{d.free_gb}GB free</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Space by Directory */}
              <div>
                <div className="flex items-center gap-1.5 mb-3">
                  <FolderOpen className="w-3 h-3 text-text-quaternary" />
                  <span className="type-overline text-text-quaternary">Space by directory</span>
                </div>
                <div className="space-y-1.5">
                  {resources.categories.slice(0, 8).map(c => (
                    <div key={c.path} className="flex items-center gap-3 py-0.5">
                      <span className="text-[11px] font-mono text-text-secondary w-[100px] shrink-0 truncate">~/{c.name}</span>
                      <div className="flex-1 h-[5px] bg-bg-tertiary rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all duration-500 ${c.drive === 'AOS-X' ? 'bg-blue' : 'bg-accent'}`}
                          style={{ width: `${Math.max(2, (c.size_gb / (resources.categories[0]?.size_gb || 1)) * 100)}%` }}
                        />
                      </div>
                      <span className="text-[10px] font-mono text-text-quaternary w-[55px] shrink-0 text-right">
                        {c.size_gb >= 1 ? `${c.size_gb.toFixed(1)}GB` : `${Math.round(c.size_gb * 1024)}MB`}
                      </span>
                      {c.is_symlink && (
                        <span className="text-[9px] text-blue px-1.5 py-0.5 rounded-[3px] bg-blue/10">AOS-X</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* Memory */}
              <div>
                <div className="flex items-center gap-1.5 mb-3">
                  <MemoryStick className="w-3 h-3 text-text-quaternary" />
                  <span className="type-overline text-text-quaternary">Memory</span>
                </div>
                <div className="space-y-1.5">
                  {resources.processes.filter(p => p.rss_mb > 50).map(p => (
                    <div key={p.category} className="flex items-center gap-3 py-0.5">
                      <span className="text-[11px] text-text-secondary w-[140px] shrink-0 truncate">{p.category}</span>
                      <div className="flex-1 h-[5px] bg-bg-tertiary rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full bg-purple transition-all duration-500"
                          style={{ width: `${Math.max(2, (p.rss_mb / resources.processes.reduce((s, x) => s + x.rss_mb, 0)) * 100)}%` }}
                        />
                      </div>
                      <span className="text-[10px] font-mono text-text-quaternary w-[55px] shrink-0 text-right">
                        {p.rss_mb >= 1024 ? `${p.rss_gb.toFixed(1)}GB` : `${Math.round(p.rss_mb)}MB`}
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Cleanable */}
              {resources.cleanables.length > 0 && (
                <div>
                  <div className="flex items-center gap-1.5 mb-3">
                    <Trash2 className="w-3 h-3 text-text-quaternary" />
                    <span className="type-overline text-text-quaternary">Cleanable</span>
                    <span className="text-[10px] font-mono text-green ml-1">
                      {resources.cleanables.reduce((s, c) => s + c.size_gb, 0).toFixed(1)}GB
                    </span>
                  </div>
                  <div className="space-y-1.5">
                    {resources.cleanables.map((c, i) => (
                      <div key={i} className="flex items-center gap-3 py-0.5">
                        <span className="text-[11px] text-text-secondary flex-1 truncate">{c.label}</span>
                        <span className="text-[10px] font-mono text-text-quaternary w-[55px] shrink-0 text-right">
                          {c.size_gb >= 1 ? `${c.size_gb.toFixed(1)}GB` : `${Math.round(c.size_gb * 1024)}MB`}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Recommendations */}
              {resources.recommendations.length > 0 && (
                <div>
                  <div className="flex items-center gap-1.5 mb-3">
                    <Lightbulb className="w-3 h-3 text-text-quaternary" />
                    <span className="type-overline text-text-quaternary">Recommendations</span>
                  </div>
                  <div className="space-y-1.5">
                    {resources.recommendations.map((r, i) => (
                      <div key={i} className="flex items-center gap-3 py-1">
                        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${r.severity === 'high' ? 'bg-red' : r.severity === 'medium' ? 'bg-yellow' : 'bg-text-quaternary'}`} />
                        <span className={`text-[11px] ${r.severity === 'high' ? 'text-red' : r.severity === 'medium' ? 'text-yellow' : 'text-text-tertiary'}`}>
                          {r.text}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Footer */}
              <div className="flex items-center justify-between pt-3 border-t border-border">
                <span className="text-[10px] text-text-quaternary">
                  Scanned {new Date(resources.scanned_at).toLocaleTimeString()}
                </span>
                <button
                  onClick={handleRefresh}
                  disabled={refreshing}
                  className="text-[10px] font-[510] text-text-tertiary hover:text-accent flex items-center gap-1.5 disabled:opacity-40 cursor-pointer transition-colors"
                >
                  <RefreshCw className={`w-2.5 h-2.5 ${refreshing ? 'animate-spin' : ''}`} />
                  Rescan
                </button>
              </div>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}

/* ---------- Service Card ---------- */
function ServiceCard({
  name,
  description,
  isOnline,
  isOnDemand,
  onRestart,
  restarting,
  onViewLogs,
  showingLogs,
}: {
  name: string;
  description: string;
  isOnline: boolean;
  isOnDemand: boolean;
  onRestart: () => void;
  restarting: boolean;
  onViewLogs: () => void;
  showingLogs: boolean;
}) {
  return (
    <div className="bg-bg-secondary rounded-[7px] p-5 border border-border hover:border-border-secondary transition-all group cursor-default"
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      {/* Header row */}
      <div className="flex items-center gap-3 mb-2">
        <div className="relative">
          <Radio className={`w-4 h-4 ${isOnline ? 'text-green' : isOnDemand ? 'text-text-quaternary' : 'text-red'}`} />
        </div>
        <h3 className="text-[15px] font-[580] text-text tracking-[-0.01em] capitalize flex-1">{name}</h3>
        <StatusDot
          color={isOnline ? 'green' : isOnDemand ? 'gray' : 'red'}
          size="md"
          pulse={!isOnline && !isOnDemand}
          label={isOnline ? 'Online' : isOnDemand ? 'On demand' : 'Offline'}
        />
      </div>

      {/* Description */}
      <p className="text-[12px] text-text-quaternary mb-4 leading-relaxed">{description}</p>

      {/* Actions */}
      <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity" style={{ transitionDuration: 'var(--duration-fast)' }}>
        <button
          onClick={onRestart}
          disabled={restarting}
          className="text-[10px] font-[510] text-text-tertiary hover:text-accent px-2 py-1 rounded-[5px] bg-bg-tertiary hover:bg-bg-quaternary transition-colors flex items-center gap-1.5 disabled:opacity-40 cursor-pointer"
        >
          <RotateCcw className="w-2.5 h-2.5" />
          {isOnline ? 'Restart' : 'Start'}
        </button>
        <button
          onClick={onViewLogs}
          className={`text-[10px] font-[510] px-2 py-1 rounded-[5px] transition-colors flex items-center gap-1.5 cursor-pointer ${
            showingLogs ? 'text-accent bg-active' : 'text-text-tertiary hover:text-accent bg-bg-tertiary hover:bg-bg-quaternary'
          }`}
        >
          <FileText className="w-2.5 h-2.5" />
          Logs
        </button>
      </div>
    </div>
  );
}

/* ---------- Services Grid ---------- */
function ServicesGrid() {
  const { data: services, isLoading } = useServices();
  const restartService = useRestartService();
  const [viewingLogs, setViewingLogs] = useState<string | null>(null);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);

  const viewLogs = useCallback(async (name: string) => {
    if (viewingLogs === name) { setViewingLogs(null); return; }
    setViewingLogs(name);
    setLogsLoading(true);
    try {
      const r = await fetchServiceLogs(name);
      setLogLines(r.lines);
    } catch {
      setLogLines(['Failed to load logs']);
    }
    setLogsLoading(false);
  }, [viewingLogs]);

  if (isLoading) return <SkeletonCards count={3} />;
  if (!services || services.length === 0) return null;

  return (
    <div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {(services ?? []).map((svc) => {
          const name = svc.name;
          const meta = SERVICE_META[name] || { description: name };
          const isOnline = svc.status === 'running' || svc.status === 'online';
          const isOnDemand = meta.onDemand && !isOnline;
          return (
            <ServiceCard
              key={name}
              name={name}
              description={meta.description}
              isOnline={isOnline}
              isOnDemand={!!isOnDemand}
              onRestart={() => restartService.mutate(name)}
              restarting={restartService.isPending}
              onViewLogs={() => viewLogs(name)}
              showingLogs={viewingLogs === name}
            />
          );
        })}
      </div>

      {/* Log viewer */}
      {viewingLogs && (
        <div className="mt-3 bg-bg-secondary rounded-[7px] p-4 border border-border">
          <div className="flex items-center justify-between mb-3">
            <span className="type-overline text-text-quaternary">{viewingLogs} logs</span>
            <button onClick={() => setViewingLogs(null)} className="text-[10px] text-text-quaternary hover:text-text-secondary cursor-pointer transition-colors">Close</button>
          </div>
          {logsLoading ? (
            <div className="text-[11px] text-text-quaternary">Loading...</div>
          ) : (
            <pre className="text-[10px] font-mono text-text-tertiary leading-[1.6] max-h-48 overflow-y-auto whitespace-pre-wrap">
              {logLines.length > 0 ? logLines.join('\n') : 'No logs available'}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

/* ---------- Cron Tier Group ---------- */
function CronTierGroup({ tier, jobs, expanded, onToggle }: { tier: number; jobs: CronJobExt[]; expanded: boolean; onToggle: () => void }) {
  const label = TIER_LABELS[tier] || 'Other';
  const failedCount = jobs.filter(j => j.status === 'failed' || j.status === 'stale').length;
  const runCron = useRunCron();
  const toggleCron = useToggleCron();
  const [viewingOutput, setViewingOutput] = useState<string | null>(null);
  const [outputLines, setOutputLines] = useState<string[]>([]);
  const [outputLoading, setOutputLoading] = useState(false);

  const viewOutput = useCallback(async (name: string) => {
    if (viewingOutput === name) { setViewingOutput(null); return; }
    setViewingOutput(name);
    setOutputLoading(true);
    try {
      const r = await fetchCronOutput(name);
      setOutputLines(r.lines);
    } catch {
      setOutputLines(['Failed']);
    }
    setOutputLoading(false);
  }, [viewingOutput]);

  return (
    <div className="mb-5">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-2.5 py-2.5 px-2 -mx-2 rounded-[5px] hover:bg-hover transition-colors cursor-pointer"
        style={{ transitionDuration: 'var(--duration-instant)' }}
      >
        {expanded
          ? <ChevronDown className="w-3 h-3 text-text-quaternary" />
          : <ChevronRight className="w-3 h-3 text-text-quaternary" />
        }
        <span className="text-[12px] font-[590] text-text-secondary">{label}</span>
        <span className={`text-[10px] font-[510] ml-1 ${failedCount === 0 ? 'text-text-quaternary' : 'text-yellow'}`}>
          {jobs.length} jobs{failedCount > 0 ? `, ${failedCount} need attention` : ''}
        </span>
      </button>

      {expanded && (
        <div className="ml-5 space-y-px">
          {jobs.map(job => (
            <div key={job.name}>
              <div
                className="h-10 flex items-center gap-3 hover:bg-hover rounded-[5px] px-3 -mx-3 transition-colors group/row"
                style={{ transitionDuration: 'var(--duration-instant)' }}
              >
                <StatusDot color={cronStatusColor(job.status)} size="md" />
                <span className="text-[13px] font-[510] text-text-secondary w-[140px] shrink-0 truncate">{job.name}</span>
                {job.description
                  ? <span className="text-[10px] text-text-quaternary flex-1 truncate hidden md:block">{job.description}</span>
                  : <span className="flex-1" />
                }
                <span className="text-[11px] text-text-quaternary w-[100px] shrink-0 text-right hidden sm:block">{job.schedule}</span>
                <span className="text-[10px] font-mono text-text-quaternary w-[60px] shrink-0 text-right">{timeAgo(job.last_run)}</span>

                {/* Action buttons */}
                <div className="flex items-center gap-0.5 opacity-0 group-hover/row:opacity-100 transition-opacity shrink-0">
                  <button
                    title="Run"
                    onClick={() => runCron.mutate(job.name)}
                    disabled={runCron.isPending}
                    className="w-7 h-7 inline-flex items-center justify-center rounded-[5px] text-text-tertiary hover:text-accent hover:bg-hover transition-colors disabled:opacity-40 cursor-pointer"
                  >
                    {runCron.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
                  </button>
                  <button
                    title="Output"
                    onClick={() => viewOutput(job.name)}
                    className={`w-7 h-7 inline-flex items-center justify-center rounded-[5px] transition-colors cursor-pointer ${
                      viewingOutput === job.name ? 'text-accent bg-active' : 'text-text-tertiary hover:text-accent hover:bg-hover'
                    }`}
                  >
                    <FileText className="w-3 h-3" />
                  </button>
                  {job.enabled ? (
                    <button
                      title="Disable"
                      onClick={() => toggleCron.mutate({ name: job.name, enabled: false })}
                      className="w-7 h-7 inline-flex items-center justify-center rounded-[5px] text-text-tertiary hover:text-red hover:bg-hover transition-colors cursor-pointer"
                    >
                      <XCircle className="w-3 h-3" />
                    </button>
                  ) : (
                    <button
                      title="Enable"
                      onClick={() => toggleCron.mutate({ name: job.name, enabled: true })}
                      className="w-7 h-7 inline-flex items-center justify-center rounded-[5px] text-text-tertiary hover:text-green hover:bg-hover transition-colors cursor-pointer"
                    >
                      <CheckCircle2 className="w-3 h-3" />
                    </button>
                  )}
                </div>
              </div>

              {/* Output viewer */}
              {viewingOutput === job.name && (
                <div className="ml-5 mr-2 mb-2 bg-bg-secondary rounded-[7px] p-4 border border-border">
                  <div className="flex items-center justify-between mb-3">
                    <span className="type-overline text-text-quaternary">{job.name} output</span>
                    <button onClick={() => setViewingOutput(null)} className="text-[10px] text-text-quaternary hover:text-text-secondary cursor-pointer transition-colors">Close</button>
                  </div>
                  {outputLoading ? (
                    <div className="text-[11px] text-text-quaternary">Loading...</div>
                  ) : (
                    <pre className="text-[10px] font-mono text-text-tertiary leading-[1.6] max-h-48 overflow-y-auto whitespace-pre-wrap">
                      {outputLines.length > 0 ? outputLines.join('\n') : 'No output'}
                    </pre>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------- Cron Jobs Section ---------- */
function CronJobsSection() {
  const { crons, isLoading } = useCrons();
  const [expandedTiers, setExpandedTiers] = useState<Set<number> | null>(null);
  const jobs = crons as CronJobExt[];
  const grouped = new Map<number, CronJobExt[]>();
  for (const job of jobs) {
    const t = job.tier ?? 0;
    if (!grouped.has(t)) grouped.set(t, []);
    grouped.get(t)!.push(job);
  }
  const sp: Record<string, number> = { failed: 0, stale: 1, ok: 2, pending: 3, disabled: 4 };
  for (const [, tj] of grouped) tj.sort((a, b) => (sp[a.status] ?? 5) - (sp[b.status] ?? 5));
  if (expandedTiers === null && crons.length > 0) {
    const init = new Set<number>([1]);
    for (const [t, tj] of grouped) {
      if (tj.some(j => j.status === 'failed' || j.status === 'stale')) init.add(t);
    }
    setExpandedTiers(init);
  }
  const toggle = useCallback((t: number) => {
    setExpandedTiers(p => { const n = new Set(p); if (n.has(t)) n.delete(t); else n.add(t); return n; });
  }, []);

  if (isLoading) return <SkeletonRows count={6} />;
  if (crons.length === 0) return <p className="text-[12px] text-text-quaternary">No cron jobs configured yet.</p>;

  return (
    <div>
      {TIER_ORDER.filter(t => grouped.has(t)).map(t => (
        <CronTierGroup key={t} tier={t} jobs={grouped.get(t)!} expanded={expandedTiers?.has(t) ?? false} onToggle={() => toggle(t)} />
      ))}
    </div>
  );
}

/* ---------- Section Header ---------- */
function SectionTitle({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div className="flex items-center gap-2.5 mb-5">
      <span className="text-text-quaternary [&>svg]:w-4 [&>svg]:h-4">{icon}</span>
      <h2 className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">{label}</h2>
    </div>
  );
}

/* ---------- Main Page ---------- */
export default function SystemPage() {
  return (
    <div className="min-h-full">
      <div className="px-6 md:px-10 py-6 md:py-8 max-w-[1200px] mx-auto overflow-y-auto h-full">
        <VerdictBanner />
        <AttentionPanel />
        <ResourcesPanel />

        <div className="mb-12">
          <SectionTitle icon={<Activity />} label="Services" />
          <ServicesGrid />
        </div>

        <div className="mb-12">
          <SectionTitle icon={<Terminal />} label="Cron Jobs" />
          <CronJobsSection />
        </div>
      </div>
    </div>
  );
}
