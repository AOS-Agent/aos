import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronDown, Clock, Folder, MessageSquare, FileText, AlertTriangle } from 'lucide-react';
import { API, formatTime12, durationText, cleanSummary, projectName } from './shared';

// ---------------------------------------------------------------------------
// Types — matches new API response
// ---------------------------------------------------------------------------

interface Session {
  id: string; title: string; project: string; start_time: string;
  duration_min: number; message_count: number; summary: string;
  tools: string[]; files: string[]; tags: string[];
}

interface PeriodTask {
  id: string; title: string; project: string; priority: number;
  completed_time?: string;
}

interface Communication {
  name: string; channel: string; direction: string;
  msg_count: number; time: string; subject: string;
}

interface Period {
  name: string; label: string; time: string;
  collapsed_summary: string;
  sessions: Session[]; tasks: PeriodTask[]; communications: Communication[];
}

interface CarryTask {
  id: string; title: string; project: string; priority: number;
  handoff_next: string; handoff_age_days: number | null; sessions_count: number;
}

interface FlowMetrics {
  total_sessions: number; total_duration_min: number;
  deep_work_min: number; longest_block_min: number;
  longest_block_period: string;
  projects: Record<string, number>; session_trend: string;
}

interface DriftPerson {
  name: string; importance: number; days_since_contact: number;
  avg_days_between: number | null; trajectory: string;
}

interface HealthData {
  steps: number | null; distance_km: number | null;
  active_energy_kcal: number | null; sleep_hours: number | null;
}

interface Weather { summary: string; }

interface DayResponse {
  date: string; day_name: string; hijri_date: string;
  headline: string;
  prayer_times: Record<string, string>;
  weather: Weather | null; health: HealthData | null;
  periods: Period[];
  flow: FlowMetrics;
  carry: { active_tasks: CarryTask[]; stale_handoffs: CarryTask[] };
  people_drift: DriftPerson[];
  reflections: string;
}

// ---------------------------------------------------------------------------
// Collapsible wrapper
// ---------------------------------------------------------------------------

function Collapsible({ open, children }: { open: boolean; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number | 'auto'>(open ? 'auto' : 0);

  useEffect(() => {
    if (!ref.current) return;
    if (open) {
      setHeight(ref.current.scrollHeight);
      const timer = setTimeout(() => setHeight('auto'), 220);
      return () => clearTimeout(timer);
    } else {
      // Set explicit height first so transition works
      setHeight(ref.current.scrollHeight);
      requestAnimationFrame(() => setHeight(0));
    }
  }, [open]);

  return (
    <div
      style={{
        height: typeof height === 'number' ? `${height}px` : 'auto',
        overflow: 'hidden',
        transition: 'height 220ms cubic-bezier(0.25, 0.46, 0.45, 0.94)',
      }}
    >
      <div ref={ref}>{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Period block
// ---------------------------------------------------------------------------

function PeriodBlock({ period, defaultOpen }: { period: Period; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const navigate = useNavigate();

  return (
    <div className="mb-2">
      {/* ── Collapsed header — always visible ── */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 py-2.5 px-1 rounded-[5px] cursor-pointer hover:bg-hover/50 transition-colors group"
      >
        {/* Dot + line */}
        <div className="flex items-center gap-2 shrink-0">
          <div className="w-1.5 h-1.5 rounded-full bg-accent/60" />
          <div className="h-px w-4 bg-border-secondary" />
        </div>

        {/* Label + time */}
        <span className="text-[11px] font-[590] uppercase tracking-[0.06em] text-accent/80">
          {period.label}
        </span>
        {period.time && (
          <span className="text-[11px] text-text-quaternary tabular-nums">
            {formatTime12(period.time)}
          </span>
        )}

        {/* Collapsed summary — only when closed */}
        {!open && (
          <span className="text-[11px] text-text-quaternary ml-auto mr-1 truncate max-w-[50%]">
            {period.collapsed_summary}
          </span>
        )}

        {/* Chevron */}
        <ChevronDown
          className={`w-3 h-3 text-text-quaternary transition-transform duration-150 ${open ? 'rotate-180' : ''} ${open ? '' : 'ml-auto'}`}
        />
      </button>

      {/* ── Expanded content ── */}
      <Collapsible open={open}>
        <div className="ml-8 pb-2">
          {/* Sessions */}
          {period.sessions.map((s) => (
            <SessionItem key={s.id} session={s} navigate={navigate} />
          ))}

          {/* Tasks completed in this period */}
          {period.tasks.length > 0 && (
            <div className="mt-2 mb-2 space-y-1">
              {period.tasks.map((t) => (
                <div key={t.id} className="flex items-baseline gap-2">
                  <span className="text-green text-[12px] shrink-0">&#10003;</span>
                  <span className="text-[13px] text-text-secondary leading-[1.5]">{t.title}</span>
                  {t.project && (
                    <button
                      type="button"
                      onClick={() => navigate(`/work?project=${t.project}`)}
                      className="text-[10px] text-text-quaternary hover:text-accent cursor-pointer shrink-0"
                    >
                      {t.project}
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Communications */}
          {period.communications.map((c, i) => (
            <div key={i} className="flex items-center gap-2 py-1 text-[12px] text-text-tertiary">
              <span>{c.channel === 'whatsapp' ? '💬' : c.channel === 'email' ? '📧' : c.channel === 'call' ? '📞' : '💬'}</span>
              <button
                type="button"
                onClick={() => navigate('/people')}
                className="text-text-secondary hover:text-accent cursor-pointer"
              >
                {c.name}
              </button>
              <span className="text-text-quaternary">·</span>
              <span className="text-text-quaternary">{c.msg_count} messages</span>
              {c.subject && (
                <>
                  <span className="text-text-quaternary">·</span>
                  <span className="text-text-quaternary truncate">{c.subject}</span>
                </>
              )}
            </div>
          ))}
        </div>
      </Collapsible>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Session item (with disclosure for files/tools)
// ---------------------------------------------------------------------------

function SessionItem({ session, navigate }: { session: Session; navigate: (path: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  const summary = cleanSummary(session.summary);
  const proj = projectName(session.project);

  return (
    <div className="mb-3">
      {/* Main session card */}
      <div
        className="py-2.5 px-3.5 rounded-[7px] bg-bg-secondary/30 border border-border/40 cursor-pointer hover:bg-bg-secondary/50 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        {/* Meta line */}
        <div className="flex items-center gap-2 text-[10px] text-text-quaternary mb-1.5">
          <Clock className="w-3 h-3" />
          <span className="tabular-nums">{formatTime12(session.start_time)}</span>
          <span className="text-border-secondary">·</span>
          <span>{durationText(session.duration_min)}</span>
          {proj && (
            <>
              <span className="text-border-secondary">·</span>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); navigate(`/work?project=${session.project}`); }}
                className="flex items-center gap-1 hover:text-accent cursor-pointer"
              >
                <Folder className="w-2.5 h-2.5" />
                {proj}
              </button>
            </>
          )}
          <span className="text-border-secondary">·</span>
          <MessageSquare className="w-2.5 h-2.5" />
          <span>{session.message_count}</span>

          {/* Link to session detail */}
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); navigate(`/sessions/${session.id}`); }}
            className="ml-auto text-text-quaternary hover:text-accent cursor-pointer text-[10px]"
          >
            →
          </button>
        </div>

        {/* Summary narrative */}
        {summary && (
          <p className="text-[13px] text-text-secondary leading-[1.55] break-words">{summary}</p>
        )}
      </div>

      {/* Disclosure: files + tools */}
      <Collapsible open={expanded}>
        <div className="px-3.5 pt-1.5 pb-1 text-[10px] text-text-quaternary space-y-1">
          {session.files.length > 0 && (
            <div className="flex items-center gap-1.5">
              <FileText className="w-3 h-3 shrink-0" />
              <span className="truncate">{session.files.join(', ')}</span>
            </div>
          )}
          {session.tools.length > 0 && (
            <div className="flex items-center gap-1.5">
              <span className="shrink-0">Tools:</span>
              <span className="truncate">{session.tools.join(', ')}</span>
            </div>
          )}
        </div>
      </Collapsible>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Flow section
// ---------------------------------------------------------------------------

function FlowSection({ flow }: { flow: FlowMetrics }) {
  if (!flow || !flow.total_sessions) return null;

  const projects = Object.entries(flow.projects)
    .sort((a, b) => b[1] - a[1])
    .map(([name, count]) => `${name} (${count})`)
    .join(', ');

  const hours = Math.floor(flow.total_duration_min / 60);
  const mins = flow.total_duration_min % 60;
  const durationStr = hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;

  const deepHours = Math.floor(flow.deep_work_min / 60);
  const deepMins = flow.deep_work_min % 60;
  const deepStr = deepHours > 0 ? `${deepHours}h ${deepMins}m` : `${deepMins}m`;

  const longestStr = durationText(flow.longest_block_min);

  const trendText = flow.session_trend === 'fading'
    ? 'Energy faded through the day — sessions got shorter.'
    : flow.session_trend === 'building'
      ? 'Momentum built through the day — sessions got longer.'
      : '';

  return (
    <div className="mt-8 pt-6 border-t border-border/40">
      <div className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary mb-3">
        Flow
      </div>
      <div className="text-[13px] text-text-tertiary leading-[1.65] space-y-1">
        <p>
          {deepStr} deep work across {flow.total_sessions} sessions ({durationStr} total).
          Longest block: {longestStr}{flow.longest_block_period ? ` during ${flow.longest_block_period}` : ''}.
        </p>
        <p>Projects: {projects}.</p>
        {trendText && <p>{trendText}</p>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Carry section
// ---------------------------------------------------------------------------

function CarrySection({ carry }: { carry: DayResponse['carry'] }) {
  const navigate = useNavigate();
  const { active_tasks, stale_handoffs } = carry;
  if (active_tasks.length === 0) return null;

  const staleIds = new Set(stale_handoffs.map((t) => t.id));

  return (
    <div className="mt-6 pt-6 border-t border-border/40">
      <div className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary mb-3">
        Carrying
      </div>
      <div className="space-y-2">
        {active_tasks.map((t) => {
          const isStale = staleIds.has(t.id);
          return (
            <div key={t.id} className="flex items-start gap-2">
              {isStale ? (
                <AlertTriangle className="w-3.5 h-3.5 text-yellow shrink-0 mt-0.5" />
              ) : (
                <span className="text-accent text-[12px] shrink-0 mt-0.5">●</span>
              )}
              <div className="min-w-0">
                <div className="flex items-baseline gap-2">
                  <span className="text-[13px] text-text-secondary leading-[1.4]">{t.title}</span>
                  <button
                    type="button"
                    onClick={() => navigate(`/work?project=${t.project}`)}
                    className="text-[10px] text-text-quaternary hover:text-accent cursor-pointer shrink-0"
                  >
                    {t.project}
                  </button>
                </div>
                {t.handoff_next && (
                  <p className="text-[11px] text-text-quaternary mt-0.5 truncate">
                    Next: {t.handoff_next}
                  </p>
                )}
                {isStale && t.handoff_age_days != null && (
                  <p className="text-[10px] text-yellow mt-0.5">
                    stale handoff — {t.handoff_age_days} days
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// People drift
// ---------------------------------------------------------------------------

function DriftSection({ people }: { people: DriftPerson[] }) {
  const navigate = useNavigate();
  if (people.length === 0) return null;

  return (
    <div className="mt-6 pt-6 border-t border-border/40">
      <div className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary mb-3">
        Connections
      </div>
      <div className="space-y-1.5">
        {people.map((p) => {
          const rhythm = p.avg_days_between ? `Every ${Math.round(p.avg_days_between)} days` : '';
          return (
            <p key={p.name} className="text-[13px] text-text-tertiary leading-[1.55]">
              <button
                type="button"
                onClick={() => navigate('/people')}
                className="text-text-secondary hover:text-accent cursor-pointer"
              >
                {p.name}
              </button>
              {' — '}{p.days_since_contact}d since contact
              {rhythm ? `. ${rhythm}.` : '.'}
              {p.trajectory === 'drifting' && <span className="text-yellow"> Drifting.</span>}
            </p>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Reflections (editable)
// ---------------------------------------------------------------------------

function ReflectionsSection({ text, date }: { text: string; date: string }) {
  const [value, setValue] = useState(text);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => { setValue(text); }, [text]);

  const save = useCallback((content: string) => {
    if (content === text && !content) return;
    setSaving(true); setSaved(false);
    fetch(`${API}/days/${date}/reflections`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: content }),
    })
      .then((r) => { if (r.ok) { setSaved(true); setTimeout(() => setSaved(false), 2000); } })
      .catch(() => {})
      .finally(() => setSaving(false));
  }, [date, text]);

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const v = e.target.value; setValue(v);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => save(v), 1500);
  }, [save]);

  const handleBlur = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    save(value);
  }, [save, value]);

  return (
    <div className="mt-6 pt-6 border-t border-border/40">
      <div className="flex items-center gap-2 mb-3">
        <div className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">
          Reflections
        </div>
        {saving && <span className="text-[9px] text-text-quaternary">Saving...</span>}
        {saved && <span className="text-[9px] text-green">Saved</span>}
      </div>
      <textarea
        value={value}
        onChange={handleChange}
        onBlur={handleBlur}
        placeholder="How was your day?"
        rows={value ? Math.max(3, value.split('\n').length + 1) : 3}
        className="w-full bg-transparent text-[13px] text-text-secondary leading-[1.65] placeholder:text-text-quaternary placeholder:italic resize-none outline-none border-b border-border/30 focus:border-accent/40 transition-colors pb-2"
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Context ribbon (weather + health — ambient, not a section)
// ---------------------------------------------------------------------------

function ContextRibbon({ weather, health }: { weather: Weather | null; health: HealthData | null }) {
  const parts: string[] = [];
  if (weather?.summary) parts.push(weather.summary);
  if (health?.steps && health.steps > 0) parts.push(`${health.steps.toLocaleString()} steps`);
  if (health?.sleep_hours) parts.push(`${health.sleep_hours}h sleep`);
  if (parts.length === 0) return null;

  return (
    <p className="text-[12px] text-text-quaternary leading-[1.5] mt-1 mb-4">
      {parts.join(' · ')}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Main DayView
// ---------------------------------------------------------------------------

export default function DayView({ date }: { date: string }) {
  const [data, setData] = useState<DayResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true); setError(null);
    fetch(`${API}/days/${date}`)
      .then((r) => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); })
      .then((d) => { setData(d); setLoading(false); })
      .catch((e) => { setError(e.message); setLoading(false); });
  }, [date]);

  if (loading) {
    return (
      <div className="animate-pulse space-y-4 mt-2">
        <div className="h-4 w-80 bg-bg-tertiary/50 rounded-[4px]" />
        <div className="h-3 w-40 bg-bg-tertiary/30 rounded-[3px]" />
        <div className="mt-6 space-y-3">
          <div className="h-8 w-full bg-bg-secondary/20 rounded-[5px]" />
          <div className="h-8 w-full bg-bg-secondary/20 rounded-[5px]" />
          <div className="h-8 w-full bg-bg-secondary/20 rounded-[5px]" />
        </div>
      </div>
    );
  }

  if (error || !data) {
    return <p className="text-red text-sm mt-4">Failed to load: {error}</p>;
  }

  // Determine if today — today's periods default open, past days collapsed
  const isToday = date === new Date().toISOString().slice(0, 10);

  return (
    <>
      {/* Hijri date */}
      {data.hijri_date && (
        <p className="text-text-quaternary text-[12px] -mt-4 mb-4">{data.hijri_date}</p>
      )}

      {/* ── Headline ── */}
      <p className="text-[15px] text-text-secondary leading-[1.6] mb-2">
        {data.headline}
      </p>

      {/* ── Context ribbon ── */}
      <ContextRibbon weather={data.weather} health={data.health} />

      {/* ── Prayer period timeline ── */}
      <div className="mt-4">
        {data.periods.map((period) => (
          <PeriodBlock key={period.name} period={period} defaultOpen={isToday} />
        ))}

        {data.periods.length === 0 && (
          <p className="text-[13px] text-text-quaternary leading-[1.6] mt-4 italic">
            A quiet day — no activity recorded.
          </p>
        )}
      </div>

      {/* ── Flow ── */}
      <FlowSection flow={data.flow} />

      {/* ── Carry ── */}
      <CarrySection carry={data.carry} />

      {/* ── People drift ── */}
      <DriftSection people={data.people_drift} />

      {/* ── Reflections ── */}
      <ReflectionsSection text={data.reflections} date={date} />
    </>
  );
}
