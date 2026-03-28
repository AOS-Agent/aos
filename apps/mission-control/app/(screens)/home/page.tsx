'use client';

import { useState, useEffect } from 'react';
import { CheckSquare, Clock, Inbox, Activity, CheckCircle2 } from 'lucide-react';
import { useWork } from '@/hooks/useWork';
import { useServices } from '@/hooks/useServices';
import { SkeletonRows } from '@/components/primitives/Skeleton';
import { ErrorBanner } from '@/components/primitives/ErrorBanner';

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 17) return 'Good afternoon';
  return 'Good evening';
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, max).trimEnd() + '...';
}

function SectionLabel({ icon: Icon, label, count }: { icon: typeof CheckSquare; label: string; count?: number }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <Icon className="w-3.5 h-3.5 text-text-quaternary" />
      <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">
        {label}
      </span>
      {count !== undefined && count > 0 && (
        <span className="text-[10px] font-[510] text-text-quaternary bg-bg-tertiary rounded-[3px] px-1.5 py-0.5 leading-none">
          {count}
        </span>
      )}
    </div>
  );
}

// TODO: read from operator.yaml via Tauri fs
const schedule = [
  { time: '08:30 – 09:45', label: 'Teaching' },
  { time: '18:00 – 20:00', label: 'Quran Garden', note: 'Tuesdays only' },
];

export default function HomePage() {
  const { data, isLoading, isError } = useWork();
  const { data: services, isLoading: servicesLoading } = useServices();

  // Hydration-safe date/greeting (avoid server/client mismatch)
  const [greeting, setGreeting] = useState('Good morning');
  const [dateStr, setDateStr] = useState('');
  useEffect(() => {
    setGreeting(getGreeting());
    setDateStr(new Date().toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' }));
  }, []);

  // Top tasks: active first, then todo sorted by priority, cap at 8
  const allTasks = data?.tasks ?? [];
  const activeTasks = allTasks.filter(t => t.status === 'active' || t.status === 'waiting');
  const todoTasks = allTasks
    .filter(t => t.status === 'todo')
    .sort((a, b) => a.priority - b.priority);
  const topTasks = [...activeTasks, ...todoTasks].slice(0, 8);

  // Recently completed (last 5, most recent first)
  const recentDone = allTasks
    .filter(t => t.status === 'done' && t.completed)
    .sort((a, b) => new Date(b.completed!).getTime() - new Date(a.completed!).getTime())
    .slice(0, 5);

  const inbox = data?.inbox ?? [];

  return (
    <div>
      {isError && <ErrorBanner />}

      {/* Greeting */}
      <h1 className="text-[22px] font-[680] text-text tracking-[-0.025em] leading-tight">
        {greeting}, Hisham
      </h1>
      <p className="text-[11px] text-text-quaternary mt-1 mb-10">
        {dateStr}
      </p>

      {/* Up Next — priority tasks */}
      <div className="mb-8">
        <SectionLabel icon={CheckSquare} label="Up Next" count={todoTasks.length + activeTasks.length} />
        {isLoading ? (
          <SkeletonRows count={4} />
        ) : topTasks.length === 0 ? (
          <p className="text-[13px] text-text-quaternary py-2">Nothing in your queue. Add tasks or check your backlog.</p>
        ) : (
          topTasks.map((task) => (
            <div
              key={task.id}
              className="h-9 flex items-center gap-3 hover:bg-hover rounded-[4px] px-3 -mx-3 transition-colors"
              style={{ transitionDuration: 'var(--duration-instant)' }}
            >
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                task.status === 'active' ? 'bg-green' :
                task.priority <= 2 ? 'bg-text-tertiary' : 'bg-text-quaternary'
              }`} />
              <span className="text-[13px] font-[510] text-text-secondary flex-1 truncate">{task.title}</span>
              <span className="text-[10px] text-text-quaternary shrink-0">{task.project || '—'}</span>
            </div>
          ))
        )}
      </div>

      {/* Schedule */}
      <div className="mb-8">
        <SectionLabel icon={Clock} label="Schedule" />
        {schedule.map((block, i) => (
          <div key={i} className="h-9 flex items-center gap-4 px-3 -mx-3">
            <span className="text-[11px] text-text-quaternary font-mono w-[110px]">{block.time}</span>
            <span className="text-[13px] font-[510] text-text-secondary">{block.label}</span>
            {block.note && (
              <span className="text-[11px] text-text-quaternary">({block.note})</span>
            )}
          </div>
        ))}
      </div>

      {/* Inbox */}
      <div className="mb-8">
        <SectionLabel icon={Inbox} label="Inbox" count={inbox.length} />
        {isLoading ? (
          <SkeletonRows count={3} />
        ) : inbox.length === 0 ? (
          <p className="text-[13px] text-text-quaternary py-2">Inbox empty</p>
        ) : (
          inbox.map((item) => (
            <div
              key={item.id}
              className="h-9 flex items-center justify-between hover:bg-hover rounded-[4px] px-3 -mx-3 transition-colors"
              style={{ transitionDuration: 'var(--duration-instant)' }}
              title={item.text}
            >
              <span className="text-[13px] text-text-secondary truncate flex-1">
                {truncate(item.text, 80)}
              </span>
              <span className="text-[10px] text-text-quaternary ml-4 whitespace-nowrap">{timeAgo(item.captured)}</span>
            </div>
          ))
        )}
      </div>

      {/* Recently Done */}
      {recentDone.length > 0 && (
        <div className="mb-8">
          <SectionLabel icon={CheckCircle2} label="Recently Done" />
          {recentDone.map((task) => (
            <div
              key={task.id}
              className="h-9 flex items-center gap-3 px-3 -mx-3"
            >
              <CheckCircle2 className="w-3.5 h-3.5 text-green shrink-0" />
              <span className="text-[13px] text-text-tertiary flex-1 truncate">{task.title}</span>
              <span className="text-[10px] text-text-quaternary shrink-0">
                {task.completed ? timeAgo(task.completed) : ''}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* System */}
      <div>
        <SectionLabel icon={Activity} label="System" />
        {servicesLoading ? (
          <SkeletonRows count={1} />
        ) : services ? (
          <div className="flex flex-wrap gap-x-5 gap-y-2 px-3 -mx-3">
            {Object.entries(services).map(([name, svc]) => (
              <div key={name} className="flex items-center gap-1.5">
                <span className={`w-[6px] h-[6px] rounded-full ${svc.status === 'online' ? 'bg-green' : 'bg-red'}`} />
                <span className="text-[11px] text-text-quaternary">{name}</span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
