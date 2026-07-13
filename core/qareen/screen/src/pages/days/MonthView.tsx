import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { API, type CalendarDay, todayStr } from './shared';

const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

interface MonthData {
  month: string;
  days: Record<string, CalendarDay>;
}

/** Build a grid of weeks for the month. Each week is 7 cells (Mon-Sun), null for empty. */
function buildGrid(year: number, month: number): (string | null)[][] {
  const firstDay = new Date(year, month - 1, 1);
  const lastDay = new Date(year, month, 0);
  const daysInMonth = lastDay.getDate();

  // Monday=0, Sunday=6 (ISO)
  let startDow = firstDay.getDay() - 1;
  if (startDow < 0) startDow = 6;

  const weeks: (string | null)[][] = [];
  let week: (string | null)[] = new Array(startDow).fill(null);

  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${year}-${String(month).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    week.push(dateStr);
    if (week.length === 7) {
      weeks.push(week);
      week = [];
    }
  }
  if (week.length > 0) {
    while (week.length < 7) week.push(null);
    weeks.push(week);
  }

  return weeks;
}

/** Warm color scale from bg-tertiary to accent based on activity score */
function cellBg(sessions: number, tasks: number): string {
  const score = sessions + tasks * 0.5;
  if (score === 0) return '';
  if (score <= 2) return 'bg-accent/5';
  if (score <= 5) return 'bg-accent/10';
  if (score <= 10) return 'bg-accent/15';
  if (score <= 20) return 'bg-accent/20';
  return 'bg-accent/25';
}

export default function MonthView({ date }: { date: string }) {
  const navigate = useNavigate();
  const [data, setData] = useState<MonthData | null>(null);
  const [loading, setLoading] = useState(true);
  const today = todayStr();

  const year = parseInt(date.substring(0, 4));
  const month = parseInt(date.substring(5, 7));
  const grid = buildGrid(year, month);

  useEffect(() => {
    setLoading(true);
    fetch(`${API}/days/${date}/calendar-context`)
      .then((r) => r.json())
      .then((d) => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [date]);

  const dayData = data?.days || {};

  // Month totals
  const totalSessions = Object.values(dayData).reduce((s, d) => s + (d.sessions || 0), 0);
  const totalTasks = Object.values(dayData).reduce((s, d) => s + (d.tasks_completed || 0), 0);
  const activeDays = Object.values(dayData).filter((d) => d.sessions > 0 || d.tasks_completed > 0).length;

  return (
    <div>
      {/* ── Day name headers ── */}
      <div className="grid grid-cols-7 gap-1 mb-1">
        {DAY_NAMES.map((name) => (
          <div key={name} className="text-center text-[10px] font-[590] uppercase tracking-wider text-text-quaternary py-1">
            {name}
          </div>
        ))}
      </div>

      {/* ── Calendar grid ── */}
      <div className="space-y-1">
        {grid.map((week, wi) => (
          <div key={wi} className="grid grid-cols-7 gap-1">
            {week.map((dateStr, di) => {
              if (!dateStr) {
                return <div key={di} className="aspect-square" />;
              }

              const dd = dayData[dateStr];
              const isToday = dateStr === today;
              const dayNum = parseInt(dateStr.substring(8, 10));
              const hasBg = dd && cellBg(dd.sessions, dd.tasks_completed);

              return (
                <button
                  key={dateStr}
                  type="button"
                  onClick={() => navigate(`/timeline/${dateStr}`)}
                  className={`
                    relative aspect-square flex flex-col items-center justify-center
                    rounded-[5px] cursor-pointer transition-colors duration-100
                    ${isToday ? 'ring-1 ring-accent/50' : ''}
                    ${hasBg || 'hover:bg-hover'}
                    ${dd ? cellBg(dd.sessions, dd.tasks_completed) : ''}
                  `}
                >
                  <span className={`text-[14px] ${isToday ? 'text-accent font-semibold' : dd?.has_log ? 'text-text-secondary' : 'text-text-quaternary'}`}>
                    {dayNum}
                  </span>
                  {dd && dd.sessions > 0 && (
                    <span className="text-[8px] text-text-quaternary tabular-nums mt-0.5">
                      {dd.sessions}s
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      {/* ── Month summary ── */}
      {!loading && (
        <p className="text-[14px] text-text-tertiary leading-[1.6] mt-6">
          {activeDays > 0
            ? `${totalSessions} sessions and ${totalTasks} tasks across ${activeDays} active days.`
            : 'No activity recorded this month.'}
        </p>
      )}
    </div>
  );
}
