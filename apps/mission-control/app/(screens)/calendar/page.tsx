'use client';

import { Calendar } from 'lucide-react';

export default function CalendarPage() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center">
      <Calendar className="w-12 h-12 text-text-quaternary mb-4" />
      <h1 className="text-xl font-bold text-text tracking-[-0.013em] mb-2">Calendar</h1>
      <p className="text-sm text-text-secondary max-w-[400px]">
        See your cron jobs, scheduled tasks, and calendar events in one view.
      </p>
      <span className="mt-4 text-[11px] text-text-quaternary font-medium uppercase tracking-wider">
        Coming in Phase 2
      </span>
    </div>
  );
}
