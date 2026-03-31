import { Calendar as CalendarIcon, Clock, Sun, Moon, Coffee } from 'lucide-react';
import { useOperator } from '@/hooks/useConfig';
import { EmptyState, SectionHeader, Skeleton, SkeletonRows } from '@/components/primitives';

function ScheduleBlock({ label, time, icon }: { label: string; time: string; icon: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 px-4 py-3 rounded-[7px] bg-bg-secondary border border-border">
      <span className="text-text-quaternary">{icon}</span>
      <div className="flex-1">
        <span className="text-[13px] font-[510] text-text-secondary">{label}</span>
      </div>
      <span className="text-[12px] font-mono text-text-tertiary">{time}</span>
    </div>
  );
}

function TodaySchedule() {
  const { data: op, isLoading } = useOperator();

  if (isLoading) return <SkeletonRows count={4} />;
  if (!op) return <p className="text-[11px] text-text-quaternary">No operator config loaded.</p>;

  const prefs = op.preferences || {};
  const blocks = [];

  if (prefs.morning_briefing_time) {
    blocks.push({ label: 'Morning Briefing', time: prefs.morning_briefing_time, icon: <Sun className="w-4 h-4" /> });
  }
  if (prefs.focus_hours_start && prefs.focus_hours_end) {
    blocks.push({ label: 'Focus Hours', time: `${prefs.focus_hours_start} - ${prefs.focus_hours_end}`, icon: <Coffee className="w-4 h-4" /> });
  }
  if (prefs.evening_checkin_time) {
    blocks.push({ label: 'Evening Check-in', time: prefs.evening_checkin_time, icon: <Moon className="w-4 h-4" /> });
  }
  if (prefs.quiet_hours_start && prefs.quiet_hours_end) {
    blocks.push({ label: 'Quiet Hours', time: `${prefs.quiet_hours_start} - ${prefs.quiet_hours_end}`, icon: <Moon className="w-4 h-4" /> });
  }

  if (blocks.length === 0) {
    return (
      <EmptyState
        icon={<CalendarIcon />}
        title="No schedule configured"
        description="Configure your daily schedule blocks in Settings to see them here."
      />
    );
  }

  return (
    <div className="space-y-2">
      {blocks.map((b, i) => (
        <ScheduleBlock key={i} label={b.label} time={b.time} icon={b.icon} />
      ))}
    </div>
  );
}

export default function CalendarPage() {
  const today = new Date();
  const dateStr = today.toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  });

  return (
    <div className="px-5 md:px-8 py-4 md:py-6 overflow-y-auto h-full">
      <h1 className="type-title mb-2">Calendar</h1>
      <p className="text-[13px] text-text-tertiary mb-6">{dateStr}</p>

      <div className="max-w-2xl">
        <SectionHeader label="Today's Schedule" icon={<Clock />} />
        <TodaySchedule />
      </div>
    </div>
  );
}
