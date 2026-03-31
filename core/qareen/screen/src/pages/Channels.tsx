import { MessageCircle, Send, Mail, Hash } from 'lucide-react';
import { useChannels } from '@/hooks/useChannels';
import { EmptyState, StatusDot, Tag, Skeleton, SkeletonRows, ErrorBanner } from '@/components/primitives';
import { ChannelType } from '@/lib/types';

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
    <div className="px-5 md:px-8 py-4 md:py-6 overflow-y-auto h-full">
      <h1 className="type-title mb-6">Channels</h1>

      {isError && <ErrorBanner message="Failed to load channel status." />}

      {isLoading ? (
        <SkeletonRows count={4} />
      ) : !data || !Array.isArray(data.channels) || data.channels.length === 0 ? (
        <EmptyState icon={<MessageCircle />} title="No channels configured" description="Communication channels will appear here when configured." />
      ) : (
        <div className="space-y-1">
          {data.channels.map((ch: any) => {
            // Handle both API shapes: { channel, connected, status } or { id, channel_type, name, is_active, is_healthy }
            const channelType = ch.channel ?? ch.channel_type ?? ch.id ?? 'unknown';
            const channelName = ch.name ?? channelType;
            const isConnected = ch.connected ?? ch.is_active ?? ch.is_healthy ?? false;
            const channelStatus = ch.status ?? (isConnected ? 'active' : 'inactive');
            const channelError = ch.error ?? null;
            const lastMessage = ch.last_message ?? null;
            return (
              <div
                key={ch.id ?? channelType}
                className="flex items-center gap-4 px-4 py-3 rounded-[7px] bg-bg-secondary border border-border hover:bg-bg-tertiary transition-colors"
                style={{ transitionDuration: 'var(--duration-instant)' }}
              >
                <span className={`text-tag-${channelColor(channelType)}`}>{channelIcon(channelType)}</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] font-[510] text-text-secondary capitalize">{channelName}</span>
                    <StatusDot color={isConnected ? 'green' : 'red'} size="sm" label={isConnected ? 'Connected' : 'Disconnected'} />
                  </div>
                  {channelError && <p className="text-[11px] text-red mt-0.5">{channelError}</p>}
                </div>
                <div className="flex items-center gap-4 shrink-0">
                  <Tag label={channelStatus} color={isConnected ? 'green' : 'red'} />
                  <span className="text-[10px] text-text-quaternary">{timeAgo(lastMessage)}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
