import { MessageCircle, Send, Mail, Hash, Radio } from 'lucide-react';
import { useChannels } from '@/hooks/useChannels';
import { EmptyState, StatusDot, Tag, SkeletonRows, ErrorBanner } from '@/components/primitives';

function channelIcon(type: string) {
  switch (type) {
    case 'telegram': return <Send className="w-4 h-4" />;
    case 'whatsapp': return <MessageCircle className="w-4 h-4" />;
    case 'email': return <Mail className="w-4 h-4" />;
    case 'slack': return <Hash className="w-4 h-4" />;
    default: return <MessageCircle className="w-4 h-4" />;
  }
}

function channelColor(type: string): 'blue' | 'green' | 'orange' | 'purple' | 'gray' {
  switch (type) {
    case 'telegram': return 'blue';
    case 'whatsapp': return 'green';
    case 'email': return 'orange';
    case 'slack': return 'purple';
    default: return 'gray';
  }
}

function timeAgo(iso: string | undefined): string {
  if (!iso) return '\u2014';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export default function ChannelsPage() {
  const { data, isLoading, isError } = useChannels();

  return (
    <div className="bg-bg min-h-full overflow-y-auto">
      <div className="max-w-[720px] mx-auto px-5 md:px-8 py-6 md:py-10">
        <h1 className="text-[22px] font-[680] text-text tracking-[-0.025em] mb-1">Channels</h1>
        <p className="text-[13px] text-text-tertiary mb-8 font-serif">Communication bridges and their status</p>

        {isError && <ErrorBanner message="Failed to load channel status." />}

        {isLoading ? (
          <SkeletonRows count={4} />
        ) : !data || !Array.isArray(data.channels) || data.channels.length === 0 ? (
          <EmptyState
            icon={<Radio />}
            title="No channels configured"
            description="Telegram, WhatsApp, email, and Slack bridges will appear here once activated."
          />
        ) : (
          <div className="space-y-2">
            {data.channels.map((ch: any) => {
              const channelType = ch.channel ?? ch.channel_type ?? ch.id ?? 'unknown';
              const channelName = ch.name ?? channelType;
              const isConnected = ch.connected ?? ch.is_active ?? ch.is_healthy ?? false;
              const channelStatus = ch.status ?? (isConnected ? 'active' : 'inactive');
              const channelError = ch.error ?? null;
              const lastMessage = ch.last_message ?? null;
              return (
                <div
                  key={ch.id ?? channelType}
                  className="flex items-center gap-4 px-4 py-3.5 rounded-[7px] bg-bg-secondary border border-border hover:border-border-secondary transition-colors cursor-default"
                  style={{ transitionDuration: 'var(--duration-instant)' }}
                >
                  <span className={`text-tag-${channelColor(channelType)}`}>{channelIcon(channelType)}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] font-[510] text-text-secondary capitalize">{channelName}</span>
                      <StatusDot color={isConnected ? 'green' : 'red'} size="sm" label={isConnected ? 'Connected' : 'Disconnected'} />
                    </div>
                    {channelError && (
                      <p className="text-[11px] text-red mt-0.5 font-serif">{channelError}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-4 shrink-0">
                    <Tag label={channelStatus} color={isConnected ? 'green' : 'red'} />
                    <span className="text-[10px] text-text-quaternary tabular-nums">{timeAgo(lastMessage)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
