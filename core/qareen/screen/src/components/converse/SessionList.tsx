import { MessageCircle, Hash, AlertCircle } from 'lucide-react';
import { StatusDot, Tag, EmptyState } from '@/components/primitives';
import type { ConverseSession } from '@/lib/converseApi';
import {
  displayName,
  statusColor,
  statusLabel,
  modeLabel,
  modeColor,
  timeAgo,
} from '@/lib/converseApi';

interface Props {
  sessions: ConverseSession[];
  selectedId: string | null;
  onSelect: (session: ConverseSession) => void;
  pendingCountBySession: Record<string, number>;
  emptyTitle: string;
  emptyDescription: string;
}

export function SessionList({
  sessions,
  selectedId,
  onSelect,
  pendingCountBySession,
  emptyTitle,
  emptyDescription,
}: Props) {
  if (sessions.length === 0) {
    return <EmptyState icon={<MessageCircle />} title={emptyTitle} description={emptyDescription} />;
  }

  return (
    <div className="flex flex-col gap-2">
      {sessions.map((s) => (
        <SessionRow
          key={s.id}
          session={s}
          active={s.id === selectedId}
          pendingCount={pendingCountBySession[s.id] ?? 0}
          onSelect={() => onSelect(s)}
        />
      ))}
    </div>
  );
}

function SessionRow({
  session,
  active,
  pendingCount,
  onSelect,
}: {
  session: ConverseSession;
  active: boolean;
  pendingCount: number;
  onSelect: () => void;
}) {
  const sColor = statusColor(session.status);
  const handling = session.status === 'handling';
  const ChannelIcon = session.channel === 'slack' ? Hash : MessageCircle;

  return (
    <button
      type="button"
      onClick={onSelect}
      className={`group text-left rounded-[10px] border transition-colors cursor-pointer px-4 py-3.5 ${
        active
          ? 'bg-bg-tertiary border-border-tertiary'
          : 'bg-bg-secondary border-border-secondary hover:border-border-tertiary hover:bg-bg-tertiary/50'
      }`}
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      <div className="flex items-center gap-2.5 mb-1.5">
        <span className="relative inline-flex items-center justify-center w-2.5 h-2.5 shrink-0">
          <span className={`w-1.5 h-1.5 rounded-full ${dotClass(sColor)} ${handling ? 'animate-pulse' : ''}`} />
          {handling && (
            <span className={`absolute inset-0 rounded-full ${dotClass(sColor)} opacity-30 animate-ping`} />
          )}
        </span>
        <span className="text-[13.5px] font-[560] text-text tracking-[-0.005em] truncate flex-1 min-w-0">
          {displayName(session)}
        </span>
        {pendingCount > 0 && (
          <span className="flex items-center gap-1 text-[10px] font-[590] text-yellow shrink-0">
            <AlertCircle className="w-3 h-3" />
            {pendingCount}
          </span>
        )}
      </div>

      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <Tag label={modeLabel(session.mode)} color={modeColor(session.mode)} />
        <span className="flex items-center gap-1 text-[10px] text-text-quaternary uppercase tracking-[0.06em]">
          <ChannelIcon className="w-3 h-3" />
          {session.channel === 'slack' ? 'Slack' : 'iMessage'}
        </span>
        <StatusDot color={sColor} label={statusLabel(session.status)} />
      </div>

      <p className="text-[12.5px] text-text-tertiary leading-[1.45] line-clamp-2 font-serif italic">
        {session.mission}
      </p>

      <div className="flex items-center justify-between mt-2">
        <span className="text-[10px] text-text-quaternary font-mono">
          {session.sent_count}/{session.max_messages} sent
        </span>
        <span className="text-[10px] text-text-quaternary font-mono">{timeAgo(session.updated_at)}</span>
      </div>
    </button>
  );
}

function dotClass(color: string): string {
  return (
    {
      green: 'bg-green',
      yellow: 'bg-yellow',
      red: 'bg-red',
      orange: 'bg-orange',
      blue: 'bg-blue',
      purple: 'bg-purple',
      gray: 'bg-text-quaternary',
    }[color] ?? 'bg-text-quaternary'
  );
}
