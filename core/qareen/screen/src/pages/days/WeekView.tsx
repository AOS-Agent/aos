import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { API } from './shared';

interface WeekDay {
  date: string;
  day_name: string;
  short_name: string;
  sessions: number;
  tasks_completed: number;
  hijri_date: string;
  has_log: boolean;
  is_today: boolean;
}

interface WeekData {
  week_start: string;
  week_end: string;
  iso_week: string;
  days: WeekDay[];
  totals: { sessions: number; tasks_completed: number };
  weekly_review: {
    title: string;
    sessions: number;
    tasks_completed: number;
    body: string;
  } | null;
}

/** Activity density → opacity class */
function densityOpacity(sessions: number, tasks: number): string {
  const score = sessions + tasks;
  if (score === 0) return 'opacity-20';
  if (score <= 3) return 'opacity-40';
  if (score <= 8) return 'opacity-60';
  if (score <= 15) return 'opacity-80';
  return 'opacity-100';
}

export default function WeekView({ date }: { date: string }) {
  const navigate = useNavigate();
  const [data, setData] = useState<WeekData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`${API}/days/${date}/week-context`)
      .then((r) => r.json())
      .then((d) => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [date]);

  if (loading) {
    return (
      <div className="animate-pulse space-y-3 mt-2">
        <div className="flex gap-2">
          {Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className="flex-1 h-28 bg-bg-secondary/30 rounded-[7px]" />
          ))}
        </div>
      </div>
    );
  }

  if (!data) {
    return <p className="text-red text-sm mt-4">Failed to load week data.</p>;
  }

  const maxSessions = Math.max(1, ...data.days.map((d) => d.sessions));

  return (
    <div>
      {/* ── 7 day cards ── */}
      <div className="grid grid-cols-7 gap-2 mb-6">
        {data.days.map((day) => {
          const barHeight = day.sessions > 0 ? Math.max(8, (day.sessions / maxSessions) * 48) : 0;
          return (
            <button
              key={day.date}
              type="button"
              onClick={() => navigate(`/timeline/${day.date}`)}
              className={`
                relative flex flex-col items-center p-2 pt-2.5 pb-3 rounded-[7px] cursor-pointer
                border transition-colors duration-100
                ${day.is_today
                  ? 'border-accent/40 bg-accent/5'
                  : day.has_log
                    ? 'border-border/60 bg-bg-secondary/30 hover:bg-bg-secondary/50'
                    : 'border-border/30 bg-transparent hover:bg-hover'
                }
              `}
            >
              {/* Day name */}
              <span className={`text-[10px] font-[590] uppercase tracking-wider mb-1 ${day.is_today ? 'text-accent' : 'text-text-quaternary'}`}>
                {day.short_name}
              </span>

              {/* Date number */}
              <span className={`text-[16px] font-semibold mb-2 ${day.is_today ? 'text-text' : day.has_log ? 'text-text-secondary' : 'text-text-quaternary'}`}>
                {new Date(day.date + 'T12:00:00').getDate()}
              </span>

              {/* Activity bar */}
              <div className="w-full flex justify-center mb-1.5" style={{ height: '48px', alignItems: 'flex-end' }}>
                {barHeight > 0 && (
                  <div
                    className={`w-3 rounded-full bg-accent ${densityOpacity(day.sessions, day.tasks_completed)}`}
                    style={{ height: `${barHeight}px` }}
                  />
                )}
              </div>

              {/* Stats */}
              <div className="text-[9px] text-text-quaternary tabular-nums space-y-0.5 text-center">
                {day.sessions > 0 && <div>{day.sessions}s</div>}
                {day.tasks_completed > 0 && <div>{day.tasks_completed}t</div>}
              </div>
            </button>
          );
        })}
      </div>

      {/* ── Week totals ── */}
      <p className="text-[14px] text-text-tertiary leading-[1.6] mb-6">
        {data.totals.sessions} sessions and {data.totals.tasks_completed} tasks across the week.
      </p>

      {/* ── Weekly review content ── */}
      {data.weekly_review && (
        <div className="mt-4">
          <div className="text-[11px] font-[590] uppercase tracking-[0.06em] text-text-quaternary mb-3">
            Weekly review
          </div>
          <div className="text-[14px] text-text-secondary leading-[1.7] prose prose-invert prose-sm max-w-none
            [&_h1]:text-[18px] [&_h1]:font-semibold [&_h1]:mt-6 [&_h1]:mb-2
            [&_h2]:text-[15px] [&_h2]:font-semibold [&_h2]:mt-5 [&_h2]:mb-2
            [&_h3]:text-[14px] [&_h3]:font-semibold [&_h3]:mt-4 [&_h3]:mb-1
            [&_ul]:list-disc [&_ul]:pl-5 [&_li]:mb-1
            [&_p]:mb-3
          ">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.weekly_review.body}</ReactMarkdown>
          </div>
        </div>
      )}

      {!data.weekly_review && (
        <p className="text-[14px] text-text-quaternary italic">
          No weekly review compiled yet.
        </p>
      )}
    </div>
  );
}
