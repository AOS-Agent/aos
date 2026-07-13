import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { API, todayStr } from './shared';

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAY_LABELS = ['Mon', '', 'Wed', '', 'Fri', '', ''];

interface YearData {
  year: string;
  days: Record<string, { sessions: number; tasks_completed: number }>;
}

/** Warm heat color — from transparent to accent */
function heatColor(sessions: number, tasks: number): string {
  const score = sessions + tasks * 0.3;
  if (score === 0) return 'bg-bg-tertiary/30';
  if (score <= 2) return 'bg-accent/15';
  if (score <= 5) return 'bg-accent/25';
  if (score <= 10) return 'bg-accent/40';
  if (score <= 20) return 'bg-accent/60';
  return 'bg-accent/80';
}

/** Build 53 columns of 7 rows (Mon-Sun), starting from Jan 1 of the year */
function buildYearGrid(year: number): { date: string; dow: number }[][] {
  const jan1 = new Date(year, 0, 1);
  // ISO weekday: Mon=0..Sun=6
  let startDow = jan1.getDay() - 1;
  if (startDow < 0) startDow = 6;

  const columns: { date: string; dow: number }[][] = [];
  let col: { date: string; dow: number }[] = [];

  // Fill empty cells before Jan 1
  for (let i = 0; i < startDow; i++) {
    col.push({ date: '', dow: i });
  }

  const d = new Date(jan1);
  while (d.getFullYear() === year) {
    const dateStr = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    let dow = d.getDay() - 1;
    if (dow < 0) dow = 6;

    col.push({ date: dateStr, dow });

    if (col.length === 7) {
      columns.push(col);
      col = [];
    }

    d.setDate(d.getDate() + 1);
  }

  // Pad last column
  if (col.length > 0) {
    while (col.length < 7) col.push({ date: '', dow: col.length });
    columns.push(col);
  }

  return columns;
}

export default function YearView({ date }: { date: string }) {
  const navigate = useNavigate();
  const [data, setData] = useState<YearData | null>(null);
  const [loading, setLoading] = useState(true);
  const today = todayStr();

  const year = parseInt(date.substring(0, 4));
  const grid = useMemo(() => buildYearGrid(year), [year]);

  useEffect(() => {
    setLoading(true);
    fetch(`${API}/days/${date}/year-context`)
      .then((r) => r.json())
      .then((d) => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [date]);

  const dayData = data?.days || {};

  // Totals
  const totalSessions = Object.values(dayData).reduce((s, d) => s + (d.sessions || 0), 0);
  const totalTasks = Object.values(dayData).reduce((s, d) => s + (d.tasks_completed || 0), 0);
  const activeDays = Object.keys(dayData).length;

  // Month label positions — find the column index where each month starts
  const monthPositions = useMemo(() => {
    const positions: { month: number; col: number }[] = [];
    let lastMonth = -1;
    grid.forEach((col, ci) => {
      for (const cell of col) {
        if (cell.date) {
          const m = parseInt(cell.date.substring(5, 7));
          if (m !== lastMonth) {
            positions.push({ month: m, col: ci });
            lastMonth = m;
          }
          break;
        }
      }
    });
    return positions;
  }, [grid]);

  const cellSize = 11;
  const cellGap = 2;
  const labelWidth = 28;
  const totalWidth = labelWidth + grid.length * (cellSize + cellGap);
  const totalHeight = 7 * (cellSize + cellGap) + 20; // +20 for month labels

  return (
    <div>
      <div className="overflow-x-auto pb-2">
        <svg width={totalWidth} height={totalHeight} className="block">
          {/* Month labels */}
          {monthPositions.map(({ month, col }) => (
            <text
              key={month}
              x={labelWidth + col * (cellSize + cellGap)}
              y={10}
              className="fill-text-quaternary"
              style={{ fontSize: '9px' }}
            >
              {MONTH_LABELS[month - 1]}
            </text>
          ))}

          {/* Day labels (Mon, Wed, Fri) */}
          {DAY_LABELS.map((label, i) =>
            label ? (
              <text
                key={i}
                x={0}
                y={20 + i * (cellSize + cellGap) + cellSize - 1}
                className="fill-text-quaternary"
                style={{ fontSize: '8px' }}
              >
                {label}
              </text>
            ) : null,
          )}

          {/* Cells */}
          {grid.map((col, ci) =>
            col.map((cell) => {
              if (!cell.date) return null;
              const dd = dayData[cell.date];
              const sessions = dd?.sessions || 0;
              const tasks = dd?.tasks_completed || 0;
              const isToday = cell.date === today;

              return (
                <rect
                  key={cell.date}
                  x={labelWidth + ci * (cellSize + cellGap)}
                  y={20 + cell.dow * (cellSize + cellGap)}
                  width={cellSize}
                  height={cellSize}
                  rx={2}
                  className={`${heatColor(sessions, tasks)} cursor-pointer hover:opacity-80 transition-opacity ${isToday ? 'stroke-accent stroke-1' : ''}`}
                  onClick={() => navigate(`/timeline/${cell.date}`)}
                >
                  <title>{`${cell.date}: ${sessions}s, ${tasks}t`}</title>
                </rect>
              );
            }),
          )}
        </svg>
      </div>

      {/* Year summary */}
      {!loading && (
        <p className="text-[14px] text-text-tertiary leading-[1.6] mt-4">
          {activeDays > 0
            ? `${totalSessions} sessions and ${totalTasks} tasks across ${activeDays} days in ${year}.`
            : `No activity recorded in ${year}.`}
        </p>
      )}
    </div>
  );
}
