import { Calendar as CalendarIcon, Clock, Sun, Moon, Coffee, Sunrise } from 'lucide-react';
import { useOperator } from '@/hooks/useConfig';
import { EmptyState, SectionHeader, SkeletonRows } from '@/components/primitives';

function ScheduleBlock({ label, time, icon, description }: { label: string; time: string; icon: React.ReactNode; description?: string }) {
  return (
    <div className="flex items-center gap-4 px-4 py-3.5 rounded-[7px] bg-bg-secondary border border-border hover:border-border-secondary transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
      <span className="text-text-quaternary shrink-0">{icon}</span>
      <div className="flex-1 min-w-0">
        <span className="text-[13px] font-[510] text-text-secondary block">{label}</span>
        {description && <span className="text-[11px] text-text-quaternary font-serif block mt-0.5">{description}</span>}
      </div>
      <span className="text-[13px] font-mono text-text-tertiary tabular-nums shrink-0">{time}</span>
    </div>
  );
}

function TodaySchedule() {
  const { data: op, isLoading } = useOperator();

  if (isLoading) return <SkeletonRows count={4} />;
  if (!op) return <p className="text-[12px] text-text-quaternary font-serif">Operator config not loaded.</p>;

  const prefs = op.preferences || {};
  const blocks: { label: string; time: string; icon: React.ReactNode; description?: string }[] = [];

  if (prefs.morning_briefing_time) {
    blocks.push({
      label: 'Morning briefing',
      time: prefs.morning_briefing_time,
      icon: <Sunrise className="w-4 h-4" />,
      description: 'Daily overview and priorities',
    });
  }
  if (prefs.focus_hours_start && prefs.focus_hours_end) {
    blocks.push({
      label: 'Focus hours',
      time: `${prefs.focus_hours_start} \u2013 ${prefs.focus_hours_end}`,
      icon: <Coffee className="w-4 h-4" />,
      description: 'Deep work, notifications paused',
    });
  }
  if (prefs.evening_checkin_time) {
    blocks.push({
      label: 'Evening check-in',
      time: prefs.evening_checkin_time,
      icon: <Sun className="w-4 h-4" />,
      description: 'Review and wind down',
    });
  }
  if (prefs.quiet_hours_start && prefs.quiet_hours_end) {
    blocks.push({
      label: 'Quiet hours',
      time: `${prefs.quiet_hours_start} \u2013 ${prefs.quiet_hours_end}`,
      icon: <Moon className="w-4 h-4" />,
      description: 'No notifications or alerts',
    });
  }

  if (blocks.length === 0) {
    return (
      <EmptyState
        icon={<CalendarIcon />}
        title="No schedule configured"
        description="Define your daily rhythm in Settings -- briefing times, focus hours, and quiet periods will show here."
      />
    );
  }

  return (
    <div className="space-y-2">
      {blocks.map((b, i) => (
        <ScheduleBlock key={i} label={b.label} time={b.time} icon={b.icon} description={b.description} />
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
    <div className="bg-bg min-h-full overflow-y-auto">
      <div className="max-w-[640px] mx-auto px-5 md:px-8 py-6 md:py-10">
        <h1 className="text-[22px] font-[680] text-text tracking-[-0.025em] mb-1">Calendar</h1>
        <p className="text-[13px] text-text-tertiary mb-8 font-serif">{dateStr}</p>

        <SectionHeader label="Today's Schedule" icon={<Clock />} />
        <TodaySchedule />
      </div>
    </div>
  );
}
