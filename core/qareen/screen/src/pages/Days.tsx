import { useEffect, useState, useRef, useCallback, useMemo } from 'react';
import { useRegisterPageActions, type PageAction } from '@/hooks/usePageActions';
import { useParams, useNavigate } from 'react-router-dom';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { todayStr, shiftDate, type ViewMode } from './days/shared';
import DayView from './days/DayView';
import WeekView from './days/WeekView';
import MonthView from './days/MonthView';
import YearView from './days/YearView';

// ---------------------------------------------------------------------------
// View detection from URL
// ---------------------------------------------------------------------------

const VIEW_SEGMENTS = new Set(['week', 'month', 'year']);

function useViewAndDate(): { view: ViewMode; date: string } {
  const params = useParams<{ '*': string }>();
  const segments = (params['*'] || '').split('/').filter(Boolean);

  let view: ViewMode = 'day';
  let date = todayStr();

  if (segments.length === 0) {
    // /timeline → day view, today
  } else if (VIEW_SEGMENTS.has(segments[0])) {
    view = segments[0] as ViewMode;
    if (segments[1]) date = segments[1];
  } else {
    date = segments[0];
  }

  return { view, date };
}

// ---------------------------------------------------------------------------
// Navigation helpers
// ---------------------------------------------------------------------------

function navPath(view: ViewMode, date: string): string {
  if (view === 'day') return `/timeline/${date}`;
  return `/timeline/${view}/${date}`;
}

function stepDate(view: ViewMode, date: string, direction: number): string {
  switch (view) {
    case 'day': return shiftDate(date, direction);
    case 'week': return shiftDate(date, direction * 7);
    case 'month': {
      const d = new Date(date + 'T12:00:00');
      d.setMonth(d.getMonth() + direction);
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }
    case 'year': {
      const d = new Date(date + 'T12:00:00');
      d.setFullYear(d.getFullYear() + direction);
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }
  }
}

function compactLabel(view: ViewMode, date: string): string {
  const d = new Date(date + 'T12:00:00');
  switch (view) {
    case 'day':
      return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    case 'week': {
      const start = new Date(date + 'T12:00:00');
      const dow = start.getDay();
      start.setDate(start.getDate() - (dow === 0 ? 6 : dow - 1));
      const end = new Date(start);
      end.setDate(end.getDate() + 6);
      return `${start.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}–${end.getDate()}`;
    }
    case 'month':
      return d.toLocaleDateString('en-US', { month: 'short', year: '2-digit' });
    case 'year':
      return String(d.getFullYear());
  }
}

const VIEW_LABELS: Record<ViewMode, string> = {
  day: 'Day', week: 'Week', month: 'Month', year: 'Year',
};
const VIEWS: ViewMode[] = ['day', 'week', 'month', 'year'];

// ---------------------------------------------------------------------------
// Floating pill — two zones: view selector (left) + date nav (right)
// ---------------------------------------------------------------------------

function TimelinePill({
  view, date, isToday, navigate,
}: {
  view: ViewMode; date: string; isToday: boolean;
  navigate: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const expandRef = useRef<HTMLDivElement>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Hover handlers — only on the view selector zone
  const enterViewZone = useCallback(() => {
    if (hideTimer.current) { clearTimeout(hideTimer.current); hideTimer.current = null; }
    setExpanded(true);
  }, []);

  const leaveViewZone = useCallback(() => {
    hideTimer.current = setTimeout(() => setExpanded(false), 400);
  }, []);

  // Also close when clicking a view
  const selectView = useCallback((key: ViewMode) => {
    if (hideTimer.current) { clearTimeout(hideTimer.current); hideTimer.current = null; }
    navigate(navPath(key, date));
    setExpanded(false);
  }, [navigate, date]);

  // Close on click outside
  useEffect(() => {
    if (!expanded) return;
    const handle = (e: MouseEvent) => {
      if (expandRef.current && !expandRef.current.contains(e.target as Node)) {
        setExpanded(false);
      }
    };
    const t = setTimeout(() => window.addEventListener('mousedown', handle), 50);
    return () => { clearTimeout(t); window.removeEventListener('mousedown', handle); };
  }, [expanded]);

  return (
    <div className="fixed top-3 left-1/2 -translate-x-1/2 z-[310] flex items-center h-8 rounded-full bg-bg-secondary/60 backdrop-blur-md border border-border/40 shadow-[0_2px_12px_rgba(0,0,0,0.3)]">

      {/* ── LEFT ZONE: View selector — hover to expand ── */}
      <div
        ref={expandRef}
        className="flex items-center h-full"
        onMouseEnter={enterViewZone}
        onMouseLeave={leaveViewZone}
      >
        {expanded ? (
          // Expanded: show all view options
          <div className="flex items-center gap-0.5 pl-1 pr-1 animate-[fade-in_150ms_ease-out]">
            {VIEWS.map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => selectView(key)}
                className={`
                  px-2.5 h-6 rounded-full text-[11px] font-[510] cursor-pointer
                  transition-colors duration-100
                  ${view === key
                    ? 'bg-bg-tertiary/80 text-text'
                    : 'text-text-quaternary hover:text-text-secondary'
                  }
                `}
              >
                {VIEW_LABELS[key]}
              </button>
            ))}
          </div>
        ) : (
          // Collapsed: show current view label — click or hover to expand
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className="flex items-center h-full pl-3 pr-2 cursor-pointer"
          >
            <span className="text-[10px] font-[590] uppercase tracking-[0.05em] text-accent/80">
              {VIEW_LABELS[view]}
            </span>
          </button>
        )}
      </div>

      {/* ── Separator ── */}
      <div className="w-px h-3.5 bg-border/50 shrink-0" />

      {/* ── RIGHT ZONE: Date navigation — always stable, no hover behavior ── */}
      <div className="flex items-center h-full pr-1.5">
        <button
          type="button"
          onClick={() => navigate(navPath(view, stepDate(view, date, -1)))}
          className="p-1 rounded-full text-text-quaternary hover:text-text-secondary cursor-pointer transition-colors"
        >
          <ChevronLeft className="w-3 h-3" />
        </button>

        <span className="text-[11px] font-[510] text-text-secondary tabular-nums px-0.5 select-none whitespace-nowrap">
          {compactLabel(view, date)}
        </span>

        <button
          type="button"
          onClick={() => navigate(navPath(view, stepDate(view, date, 1)))}
          className="p-1 rounded-full text-text-quaternary hover:text-text-secondary cursor-pointer transition-colors"
        >
          <ChevronRight className="w-3 h-3" />
        </button>

        {!isToday && (
          <>
            <div className="w-px h-3.5 bg-border/50 mx-0.5 shrink-0" />
            <button
              type="button"
              onClick={() => navigate(navPath(view, todayStr()))}
              className="px-2 h-5 rounded-full text-[10px] font-[510] text-accent bg-accent/10 hover:bg-accent/15 cursor-pointer transition-colors"
            >
              Today
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main shell
// ---------------------------------------------------------------------------

export default function Days() {
  const { view, date } = useViewAndDate();
  const navigate = useNavigate();
  const isToday = date === todayStr();

  // Keyboard navigation — only when document is focused and no modifier keys
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.isTrusted) return;
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        navigate(navPath(view, stepDate(view, date, -1)));
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        navigate(navPath(view, stepDate(view, date, 1)));
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [view, date, navigate]);

  const pageActions: PageAction[] = useMemo(() => [
    {
      id: 'timeline.switch_view',
      label: 'Switch timeline view',
      category: 'navigate',
      params: [{ name: 'view', type: 'enum' as const, required: true, description: 'View mode', options: ['day', 'week', 'month', 'year'] }],
      execute: ({ view: v }) => navigate(navPath(v as ViewMode, date)),
    },
    {
      id: 'timeline.go_today',
      label: 'Go to today',
      category: 'navigate',
      execute: () => navigate(navPath(view, todayStr())),
    },
    {
      id: 'timeline.go_forward',
      label: 'Go forward one period',
      category: 'navigate',
      execute: () => navigate(navPath(view, stepDate(view, date, 1))),
    },
    {
      id: 'timeline.go_back',
      label: 'Go back one period',
      category: 'navigate',
      execute: () => navigate(navPath(view, stepDate(view, date, -1))),
    },
  ], [view, date, navigate])
  useRegisterPageActions(pageActions)

  return (
    <div className="h-full overflow-y-auto overflow-x-hidden">
      <TimelinePill view={view} date={date} isToday={isToday} navigate={navigate} />

      <div className="max-w-[680px] mx-auto px-5 pt-14 pb-24 overflow-hidden">
        {view === 'day' && <DayView date={date} />}
        {view === 'week' && <WeekView date={date} />}
        {view === 'month' && <MonthView date={date} />}
        {view === 'year' && <YearView date={date} />}
      </div>
    </div>
  );
}
