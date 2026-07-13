// Days — shared types and helpers

export const API = '/api';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PrayerTimes {
  fajr?: string;
  sunrise?: string;
  dhuhr?: string;
  asr?: string;
  maghrib?: string;
  isha?: string;
}

export interface Session {
  id: string;
  title: string;
  project: string;
  start_time: string;
  duration_min: number;
  message_count: number;
  summary: string;
  tools: string[];
  files: string[];
  tags: string[];
  prayer_period: string;
}

export interface TaskInfo {
  id: string;
  title: string;
  project: string;
  priority: number;
  status: string;
}

export interface DriftingPerson {
  name: string;
  importance: number;
  days_since_contact: number;
  avg_days_between: number | null;
  trajectory: string;
  last_channel: string;
}

export interface HealthData {
  steps: number | null;
  distance_km: number | null;
  active_energy_kcal: number | null;
  sleep_hours: number | null;
  resting_hr: number | null;
}

export interface Weather {
  condition: string;
  temp_high: string;
  temp_low: string;
  humidity: string;
  summary: string;
}

export interface DayData {
  date: string;
  day_name: string;
  hijri_date: string;
  weather: Weather | null;
  prayer_times: PrayerTimes;
  sessions: Session[];
  tasks: {
    completed: TaskInfo[];
    started: TaskInfo[];
    active: TaskInfo[];
  };
  people: {
    drifting: DriftingPerson[];
    recent_interactions: unknown[];
  };
  health: HealthData | null;
  reflections: string;
  meta: {
    has_daily_log: boolean;
    sessions_count: number;
    tasks_completed_count: number;
  };
}

export interface CalendarDay {
  sessions: number;
  tasks_completed: number;
  has_log: boolean;
}

export type ViewMode = 'day' | 'week' | 'month' | 'year';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function shiftDate(dateStr: string, days: number): string {
  const d = new Date(dateStr + 'T12:00:00');
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function formatTime12(time24: string): string {
  if (!time24) return '';
  const [h, m] = time24.split(':').map(Number);
  const suffix = h >= 12 ? 'PM' : 'AM';
  const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
  return `${h12}:${String(m).padStart(2, '0')} ${suffix}`;
}

export function durationText(min: number): string {
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

export function cleanSummary(raw: string): string {
  if (!raw) return '';
  return raw
    .replace(/\*\*Started with\*\*:\s*/i, '')
    .replace(/\*\*Ended with\*\*:\s*/i, '')
    .replace(/\*\*Files\*\*:\s*[^\n]*/i, '')
    .replace(/\*\*Tools\*\*:\s*[^\n]*/i, '')
    .replace(/\*\*/g, '')
    .split('\n')
    .filter((l) => l.trim())
    .slice(0, 2)
    .join(' ')
    .trim();
}

export function projectName(raw: string): string {
  if (!raw) return '';
  const parts = raw.split('-').filter(Boolean);
  return parts[parts.length - 1] || raw;
}

/** Get the Monday of the week containing the given date */
export function getWeekStart(dateStr: string): string {
  const d = new Date(dateStr + 'T12:00:00');
  const day = d.getDay();
  const diff = day === 0 ? -6 : 1 - day; // Monday is 1, Sunday is 0
  d.setDate(d.getDate() + diff);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Get ISO week number */
export function getISOWeek(dateStr: string): number {
  const d = new Date(dateStr + 'T12:00:00');
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  return Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
}

/** Get first day of month */
export function getMonthStart(dateStr: string): string {
  return dateStr.substring(0, 7) + '-01';
}

/** Format: "Apr 2" */
export function shortDate(dateStr: string): string {
  const d = new Date(dateStr + 'T12:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

/** Format: "April 2026" */
export function monthYearLabel(dateStr: string): string {
  const d = new Date(dateStr + 'T12:00:00');
  return d.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
}
