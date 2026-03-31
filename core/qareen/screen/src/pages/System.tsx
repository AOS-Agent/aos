import { useState, useCallback } from 'react';
import {
  Activity, Terminal, HardDrive, Cpu, ChevronDown, ChevronRight,
  Play, RotateCcw, FileText, AlertTriangle, XCircle, Clock,
  CheckCircle2, Loader2, RefreshCw, Trash2,
  MemoryStick, FolderOpen, Lightbulb,
} from 'lucide-react';
import { useServices } from '@/hooks/useServices';
import { useCrons, type CronJob } from '@/hooks/useCrons';
import { useHealth } from '@/hooks/useHealth';
import { useAttention, type AttentionItem } from '@/hooks/useAttention';
import { useResources, useRefreshResources } from '@/hooks/useResources';
import { useRestartService, useRunCron, useToggleCron, fetchCronOutput, fetchServiceLogs } from '@/hooks/useSystemActions';
import { SkeletonRows, SkeletonCards } from '@/components/primitives/Skeleton';
import { SectionHeader } from '@/components/primitives/SectionHeader';
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
function GaugeBar({ pct }: { pct: number }) {
  return <div className="w-20 h-1.5 bg-bg-tertiary rounded-full overflow-hidden"><div className={`h-full rounded-full transition-all ${barColor(pct)}`} style={{ width: `${pct}%` }} /></div>;
}

function VerdictBanner() {
  const { data, isLoading } = useAttention();
  if (isLoading) return <div className="bg-bg-secondary rounded-[8px] p-4 mb-6 animate-pulse"><div className="h-5 w-48 bg-bg-tertiary rounded" /><div className="h-3 w-64 bg-bg-tertiary rounded mt-2" /></div>;
  if (!data) return null;
  const dotColor = data.verdict === 'healthy' ? 'bg-green' : data.verdict === 'warning' ? 'bg-yellow' : 'bg-red';
  const bg = data.verdict === 'healthy' ? 'bg-green-muted' : data.verdict === 'warning' ? 'bg-yellow-muted' : 'bg-red-muted';
  return (
    <div className={`${bg} rounded-[8px] p-4 mb-6`}>
      <div className="flex items-center gap-2.5"><span className={`w-2 h-2 rounded-full ${dotColor} ${data.verdict !== 'healthy' ? 'animate-pulse' : ''}`} /><span className="text-[15px] font-[600] text-text tracking-[-0.01em]">{data.verdict_text}</span></div>
      <p className="text-[11px] text-text-tertiary mt-1 ml-[18px]">{data.summary}</p>
    </div>
  );
}

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
    <div className="mb-8 space-y-1">
      {data.items.map((item, i) => (
        <div key={i} className="flex items-center gap-3 px-3 py-2.5 rounded-[6px] bg-bg-secondary hover:bg-bg-tertiary transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
          <span className={item.type === 'error' ? 'text-red' : 'text-yellow'}>{iconFor(item)}</span>
          <div className="flex-1 min-w-0"><span className="text-[13px] font-[510] text-text">{item.text}</span>{item.detail && <span className="text-[11px] text-text-quaternary ml-2">{item.detail}</span>}</div>
          {item.action_type === 'restart_service' && item.action_target && <button onClick={() => restartService.mutate(item.action_target!)} disabled={restartService.isPending} className="text-[10px] font-[590] text-accent px-2 py-1 rounded bg-bg-tertiary hover:bg-bg-quaternary transition-colors disabled:opacity-40">{restartService.isPending ? 'Restarting...' : 'Restart'}</button>}
          {item.action_type === 'run_cron' && item.action_target && <button onClick={() => runCron.mutate(item.action_target!)} disabled={runCron.isPending} className="text-[10px] font-[590] text-accent px-2 py-1 rounded bg-bg-tertiary hover:bg-bg-quaternary transition-colors disabled:opacity-40">{runCron.isPending ? 'Running...' : 'Run now'}</button>}
        </div>
      ))}
    </div>
  );
}

function ResourcesPanel() {
  const { data: health, isLoading: healthLoading } = useHealth();
  const [expanded, setExpanded] = useState(false);
  const { data: resources, isLoading: resourcesLoading } = useResources(expanded);
  const refreshResources = useRefreshResources();
  const [refreshing, setRefreshing] = useState(false);
  const handleRefresh = useCallback(async () => { setRefreshing(true); try { await refreshResources(); } catch { /* empty */ } setRefreshing(false); }, [refreshResources]);
  if (healthLoading || !health) return <div className="flex gap-6 mb-8"><div className="h-4 w-32 bg-bg-tertiary rounded animate-pulse" /><div className="h-4 w-32 bg-bg-tertiary rounded animate-pulse" /></div>;
  return (
    <div className="mb-8">
      <button onClick={() => setExpanded(!expanded)} className="w-full flex items-center gap-4 md:gap-6 py-2 px-1 -mx-1 rounded-sm hover:bg-hover transition-colors group flex-wrap" style={{ transitionDuration: 'var(--duration-instant)' }}>
        <div className="flex items-center gap-2"><HardDrive className="w-3 h-3 text-text-quaternary" /><span className="text-[11px] text-text-quaternary">Disk</span><GaugeBar pct={health.disk_pct} /><span className={`text-[10px] font-mono ${textColor(health.disk_pct)}`}>{health.disk_pct}%</span><span className="text-[10px] font-mono text-text-quaternary hidden sm:inline">{health.disk_free_gb}GB free</span></div>
        <div className="flex items-center gap-2"><Cpu className="w-3 h-3 text-text-quaternary" /><span className="text-[11px] text-text-quaternary">RAM</span><GaugeBar pct={health.ram_pct} /><span className={`text-[10px] font-mono ${textColor(health.ram_pct)}`}>{health.ram_pct}%</span><span className="text-[10px] font-mono text-text-quaternary hidden sm:inline">{health.ram_used_gb}GB</span></div>
        <span className="ml-auto text-text-quaternary opacity-0 group-hover:opacity-100 transition-opacity">{expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}</span>
      </button>
      {expanded && (
        <div className="mt-3 bg-bg-secondary rounded-[8px] p-4 border border-border space-y-5">
          {resourcesLoading ? <div className="space-y-3"><div className="h-20 bg-bg-tertiary rounded animate-pulse" /></div> : resources ? (
            <>
              <div>
                <div className="flex items-center gap-1.5 mb-2"><HardDrive className="w-3 h-3 text-text-quaternary" /><span className="type-overline text-text-quaternary">Drives</span></div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {resources.drives.map(d => (
                    <div key={d.mount} className="bg-bg-tertiary rounded-[6px] p-3">
                      <div className="flex items-center justify-between mb-2"><span className="text-[12px] font-[510] text-text-secondary">{d.label}</span><span className={`text-[10px] font-mono ${textColor(d.pct)}`}>{d.pct}%</span></div>
                      <div className="w-full h-2 bg-bg-quaternary rounded-full overflow-hidden mb-1.5"><div className={`h-full rounded-full ${barColor(d.pct)}`} style={{ width: `${d.pct}%` }} /></div>
                      <div className="flex justify-between text-[10px] font-mono text-text-quaternary"><span>{d.used_gb}GB used</span><span>{d.free_gb}GB free</span></div>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <div className="flex items-center gap-1.5 mb-2"><FolderOpen className="w-3 h-3 text-text-quaternary" /><span className="type-overline text-text-quaternary">Space by Directory</span></div>
                <div className="space-y-1">{resources.categories.slice(0, 8).map(c => (
                  <div key={c.path} className="flex items-center gap-2 py-0.5"><span className="text-[11px] font-mono text-text-secondary w-[100px] shrink-0 truncate">~/{c.name}</span><div className="flex-1 h-1.5 bg-bg-tertiary rounded-full overflow-hidden"><div className={`h-full rounded-full ${c.drive === 'AOS-X' ? 'bg-blue' : 'bg-accent'}`} style={{ width: `${Math.max(2, (c.size_gb / (resources.categories[0]?.size_gb || 1)) * 100)}%` }} /></div><span className="text-[10px] font-mono text-text-quaternary w-[55px] shrink-0 text-right">{c.size_gb >= 1 ? `${c.size_gb.toFixed(1)}GB` : `${Math.round(c.size_gb * 1024)}MB`}</span>{c.is_symlink && <span className="text-[9px] text-blue px-1 py-px rounded bg-blue/10">AOS-X</span>}</div>
                ))}</div>
              </div>
              <div>
                <div className="flex items-center gap-1.5 mb-2"><MemoryStick className="w-3 h-3 text-text-quaternary" /><span className="type-overline text-text-quaternary">Memory</span></div>
                <div className="space-y-1">{resources.processes.filter(p => p.rss_mb > 50).map(p => (
                  <div key={p.category} className="flex items-center gap-2 py-0.5"><span className="text-[11px] text-text-secondary w-[140px] shrink-0 truncate">{p.category}</span><div className="flex-1 h-1.5 bg-bg-tertiary rounded-full overflow-hidden"><div className="h-full rounded-full bg-purple" style={{ width: `${Math.max(2, (p.rss_mb / resources.processes.reduce((s, x) => s + x.rss_mb, 0)) * 100)}%` }} /></div><span className="text-[10px] font-mono text-text-quaternary w-[55px] shrink-0 text-right">{p.rss_mb >= 1024 ? `${p.rss_gb.toFixed(1)}GB` : `${Math.round(p.rss_mb)}MB`}</span></div>
                ))}</div>
              </div>
              {resources.cleanables.length > 0 && <div><div className="flex items-center gap-1.5 mb-2"><Trash2 className="w-3 h-3 text-text-quaternary" /><span className="type-overline text-text-quaternary">Cleanable</span><span className="text-[10px] font-mono text-green ml-1">{resources.cleanables.reduce((s, c) => s + c.size_gb, 0).toFixed(1)}GB</span></div><div className="space-y-1">{resources.cleanables.map((c, i) => <div key={i} className="flex items-center gap-2 py-0.5"><span className="text-[11px] text-text-secondary flex-1 truncate">{c.label}</span><span className="text-[10px] font-mono text-text-quaternary w-[55px] shrink-0 text-right">{c.size_gb >= 1 ? `${c.size_gb.toFixed(1)}GB` : `${Math.round(c.size_gb * 1024)}MB`}</span></div>)}</div></div>}
              {resources.recommendations.length > 0 && <div><div className="flex items-center gap-1.5 mb-2"><Lightbulb className="w-3 h-3 text-text-quaternary" /><span className="type-overline text-text-quaternary">Recommendations</span></div><div className="space-y-1">{resources.recommendations.map((r, i) => <div key={i} className="flex items-center gap-2 py-1"><span className={`w-1.5 h-1.5 rounded-full shrink-0 ${r.severity === 'high' ? 'bg-red' : r.severity === 'medium' ? 'bg-yellow' : 'bg-text-quaternary'}`} /><span className={`text-[11px] ${r.severity === 'high' ? 'text-red' : r.severity === 'medium' ? 'text-yellow' : 'text-text-tertiary'}`}>{r.text}</span></div>)}</div></div>}
              <div className="flex items-center justify-between pt-2 border-t border-border"><span className="text-[10px] text-text-quaternary">Scanned {new Date(resources.scanned_at).toLocaleTimeString()}</span><button onClick={handleRefresh} disabled={refreshing} className="text-[10px] font-[510] text-text-tertiary hover:text-accent flex items-center gap-1 disabled:opacity-40"><RefreshCw className={`w-2.5 h-2.5 ${refreshing ? 'animate-spin' : ''}`} />Rescan</button></div>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}

function ServicesGrid() {
  const { data: services, isLoading } = useServices();
  const restartService = useRestartService();
  const [viewingLogs, setViewingLogs] = useState<string | null>(null);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);
  const viewLogs = useCallback(async (name: string) => { if (viewingLogs === name) { setViewingLogs(null); return; } setViewingLogs(name); setLogsLoading(true); try { const r = await fetchServiceLogs(name); setLogLines(r.lines); } catch { setLogLines(['Failed to load logs']); } setLogsLoading(false); }, [viewingLogs]);
  if (isLoading) return <SkeletonCards count={3} />;
  if (!services || services.length === 0) return null;
  return (
    <div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        {(services ?? []).map((svc) => {
          const name = svc.name;
          const meta = SERVICE_META[name] || { description: name };
          const isOnline = svc.status === 'running' || svc.status === 'online';
          const isOnDemand = meta.onDemand && !isOnline;
          return (
            <div key={name} className="bg-bg-secondary rounded-[7px] p-4 hover:bg-bg-tertiary transition-colors group" style={{ transitionDuration: 'var(--duration-instant)' }}>
              <div className="flex items-center gap-2 mb-1"><StatusDot color={isOnline ? 'green' : isOnDemand ? 'gray' : 'red'} size="md" pulse={!isOnline && !isOnDemand} /><span className="text-[13px] font-[510] text-text capitalize">{name}</span></div>
              <p className="text-[11px] text-text-quaternary mb-3">{meta.description}</p>
              <div className="flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                <button onClick={() => restartService.mutate(name)} disabled={restartService.isPending} className="text-[10px] font-[510] text-text-tertiary hover:text-accent px-1.5 py-0.5 rounded bg-bg-tertiary hover:bg-bg-quaternary transition-colors flex items-center gap-1 disabled:opacity-40"><RotateCcw className="w-2.5 h-2.5" />{isOnline ? 'Restart' : 'Start'}</button>
                <button onClick={() => viewLogs(name)} className="text-[10px] font-[510] text-text-tertiary hover:text-accent px-1.5 py-0.5 rounded bg-bg-tertiary hover:bg-bg-quaternary transition-colors flex items-center gap-1"><FileText className="w-2.5 h-2.5" />Logs</button>
              </div>
            </div>
          );
        })}
      </div>
      {viewingLogs && <div className="mt-2 bg-bg-secondary rounded-[7px] p-3 border border-border"><div className="flex items-center justify-between mb-2"><span className="type-overline text-text-quaternary">{viewingLogs} logs</span><button onClick={() => setViewingLogs(null)} className="text-[10px] text-text-quaternary hover:text-text-secondary">Close</button></div>{logsLoading ? <div className="text-[11px] text-text-quaternary">Loading...</div> : <pre className="text-[10px] font-mono text-text-tertiary leading-[1.6] max-h-48 overflow-y-auto whitespace-pre-wrap">{logLines.length > 0 ? logLines.join('\n') : 'No logs available'}</pre>}</div>}
    </div>
  );
}

function CronTierGroup({ tier, jobs, expanded, onToggle }: { tier: number; jobs: CronJobExt[]; expanded: boolean; onToggle: () => void }) {
  const label = TIER_LABELS[tier] || 'Other';
  const failedCount = jobs.filter(j => j.status === 'failed' || j.status === 'stale').length;
  const runCron = useRunCron();
  const toggleCron = useToggleCron();
  const [viewingOutput, setViewingOutput] = useState<string | null>(null);
  const [outputLines, setOutputLines] = useState<string[]>([]);
  const [outputLoading, setOutputLoading] = useState(false);
  const viewOutput = useCallback(async (name: string) => { if (viewingOutput === name) { setViewingOutput(null); return; } setViewingOutput(name); setOutputLoading(true); try { const r = await fetchCronOutput(name); setOutputLines(r.lines); } catch { setOutputLines(['Failed']); } setOutputLoading(false); }, [viewingOutput]);

  return (
    <div className="mb-4">
      <button onClick={onToggle} className="w-full flex items-center gap-2 py-2 px-1 -mx-1 rounded-sm hover:bg-hover transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
        {expanded ? <ChevronDown className="w-3 h-3 text-text-quaternary" /> : <ChevronRight className="w-3 h-3 text-text-quaternary" />}
        <span className="text-[12px] font-[590] text-text-secondary">{label}</span>
        <span className={`text-[10px] font-[510] ml-1 ${failedCount === 0 ? 'text-text-quaternary' : 'text-yellow'}`}>{jobs.length} jobs{failedCount > 0 ? `, ${failedCount} need attention` : ''}</span>
      </button>
      {expanded && <div className="ml-5 space-y-px">{jobs.map(job => (
        <div key={job.name}>
          <div className="h-9 flex items-center gap-3 hover:bg-hover rounded-sm px-2 -mx-2 transition-colors group/row" style={{ transitionDuration: 'var(--duration-instant)' }}>
            <StatusDot color={cronStatusColor(job.status)} size="md" />
            <span className="text-[13px] font-[510] text-text-secondary w-[140px] shrink-0 truncate">{job.name}</span>
            {job.description ? <span className="text-[10px] text-text-quaternary flex-1 truncate hidden md:block">{job.description}</span> : <span className="flex-1" />}
            <span className="text-[11px] text-text-quaternary w-[100px] shrink-0 text-right hidden sm:block">{job.schedule}</span>
            <span className="text-[10px] font-mono text-text-quaternary w-[60px] shrink-0 text-right">{timeAgo(job.last_run)}</span>
            <div className="flex items-center gap-0.5 opacity-0 group-hover/row:opacity-100 transition-opacity shrink-0">
              <button title="Run" onClick={() => runCron.mutate(job.name)} disabled={runCron.isPending} className="w-6 h-6 inline-flex items-center justify-center rounded-sm text-text-tertiary hover:text-accent hover:bg-hover transition-colors disabled:opacity-40">{runCron.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}</button>
              <button title="Output" onClick={() => viewOutput(job.name)} className={`w-6 h-6 inline-flex items-center justify-center rounded-sm transition-colors ${viewingOutput === job.name ? 'text-accent bg-active' : 'text-text-tertiary hover:text-accent hover:bg-hover'}`}><FileText className="w-3 h-3" /></button>
              {job.enabled ? <button title="Disable" onClick={() => toggleCron.mutate({ name: job.name, enabled: false })} className="w-6 h-6 inline-flex items-center justify-center rounded-sm text-text-tertiary hover:text-red hover:bg-hover transition-colors"><XCircle className="w-3 h-3" /></button> : <button title="Enable" onClick={() => toggleCron.mutate({ name: job.name, enabled: true })} className="w-6 h-6 inline-flex items-center justify-center rounded-sm text-text-tertiary hover:text-green hover:bg-hover transition-colors"><CheckCircle2 className="w-3 h-3" /></button>}
            </div>
          </div>
          {viewingOutput === job.name && <div className="ml-5 mr-2 mb-2 bg-bg-secondary rounded-[6px] p-3 border border-border"><div className="flex items-center justify-between mb-2"><span className="type-overline text-text-quaternary">{job.name} output</span><button onClick={() => setViewingOutput(null)} className="text-[10px] text-text-quaternary hover:text-text-secondary">Close</button></div>{outputLoading ? <div className="text-[11px] text-text-quaternary">Loading...</div> : <pre className="text-[10px] font-mono text-text-tertiary leading-[1.6] max-h-48 overflow-y-auto whitespace-pre-wrap">{outputLines.length > 0 ? outputLines.join('\n') : 'No output'}</pre>}</div>}
        </div>
      ))}</div>}
    </div>
  );
}

function CronJobsSection() {
  const { crons, isLoading } = useCrons();
  const [expandedTiers, setExpandedTiers] = useState<Set<number> | null>(null);
  const jobs = crons as CronJobExt[];
  const grouped = new Map<number, CronJobExt[]>();
  for (const job of jobs) { const t = job.tier ?? 0; if (!grouped.has(t)) grouped.set(t, []); grouped.get(t)!.push(job); }
  const sp: Record<string, number> = { failed: 0, stale: 1, ok: 2, pending: 3, disabled: 4 };
  for (const [, tj] of grouped) tj.sort((a, b) => (sp[a.status] ?? 5) - (sp[b.status] ?? 5));
  if (expandedTiers === null && crons.length > 0) { const init = new Set<number>([1]); for (const [t, tj] of grouped) { if (tj.some(j => j.status === 'failed' || j.status === 'stale')) init.add(t); } setExpandedTiers(init); }
  const toggle = useCallback((t: number) => { setExpandedTiers(p => { const n = new Set(p); if (n.has(t)) n.delete(t); else n.add(t); return n; }); }, []);
  if (isLoading) return <SkeletonRows count={6} />;
  if (crons.length === 0) return <p className="text-[11px] text-text-quaternary">No cron jobs</p>;
  return <div>{TIER_ORDER.filter(t => grouped.has(t)).map(t => <CronTierGroup key={t} tier={t} jobs={grouped.get(t)!} expanded={expandedTiers?.has(t) ?? false} onToggle={() => toggle(t)} />)}</div>;
}

export default function SystemPage() {
  return (
    <div className="px-5 md:px-8 py-4 md:py-6 overflow-y-auto h-full">
      <h1 className="type-title mb-6">System Health</h1>
      <VerdictBanner />
      <AttentionPanel />
      <ResourcesPanel />
      <div className="mb-10"><SectionHeader label="Services" icon={<Activity />} /><ServicesGrid /></div>
      <div><SectionHeader label="Cron Jobs" icon={<Terminal />} /><CronJobsSection /></div>
    </div>
  );
}
