import { useState, useMemo } from 'react';
import { Search, Users, X, MessageCircle, Phone, Mail, Hash, AtSign, ArrowUpRight, ChevronRight } from 'lucide-react';
import { usePeople, usePerson, usePersonSurfaces } from '@/hooks/usePeople';
import { EmptyState } from '@/components/primitives/EmptyState';
import { Tag, type TagColor } from '@/components/primitives/Tag';
import { SectionHeader } from '@/components/primitives/SectionHeader';
import { Skeleton } from '@/components/primitives/Skeleton';
import { SkeletonRows } from '@/components/primitives/Skeleton';
import { ErrorBanner } from '@/components/primitives/ErrorBanner';
import { TabBar } from '@/components/primitives/TabBar';
import type { PersonResponse, PersonSurfaceItem, InteractionSchema } from '@/lib/types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function timeAgo(iso: string | undefined): string {
  if (!iso) return 'never';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  if (days < 30) return `${Math.floor(days / 7)}w ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

function recencyColor(iso: string | undefined): 'green' | 'yellow' | 'orange' | 'gray' {
  if (!iso) return 'gray';
  const days = (Date.now() - new Date(iso).getTime()) / 86400000;
  if (days < 3) return 'green';
  if (days < 14) return 'yellow';
  if (days < 60) return 'orange';
  return 'gray';
}

function channelIcon(channel: string) {
  const c = channel.toLowerCase();
  if (c === 'whatsapp' || c === 'telegram' || c === 'sms') return <MessageCircle className="w-3 h-3" />;
  if (c === 'phone' || c === 'call') return <Phone className="w-3 h-3" />;
  if (c === 'email') return <Mail className="w-3 h-3" />;
  if (c === 'slack') return <Hash className="w-3 h-3" />;
  return <AtSign className="w-3 h-3" />;
}

const channelTagColor: Record<string, TagColor> = {
  whatsapp: 'green',
  telegram: 'blue',
  email: 'purple',
  slack: 'teal',
  sms: 'orange',
  phone: 'yellow',
};

function Initials({ name, size = 'md' }: { name: string; size?: 'sm' | 'md' | 'lg' }) {
  const letters = name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
  const dims = size === 'lg' ? 'w-14 h-14 text-[18px]' : size === 'md' ? 'w-10 h-10 text-[14px]' : 'w-8 h-8 text-[12px]';
  return (
    <div className={`${dims} rounded-full bg-bg-tertiary border border-border-secondary flex items-center justify-center font-[590] text-text-tertiary shrink-0 transition-colors`} style={{ transitionDuration: 'var(--duration-instant)' }}>
      {letters}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Person Card — profile-style, not database row
// ---------------------------------------------------------------------------

function PersonCard({ person, active, onClick }: { person: PersonResponse; active: boolean; onClick: () => void }) {
  const recency = recencyColor(person.last_interaction);
  const channels = person.channels ? Object.keys(person.channels) : [];

  return (
    <button
      onClick={onClick}
      className={`
        group w-full text-left px-5 py-4 transition-all cursor-pointer
        border-b border-border
        ${active
          ? 'bg-selected'
          : 'hover:bg-hover'
        }
      `}
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      <div className="flex items-start gap-3.5">
        <div className="relative mt-0.5">
          <Initials name={person.name} size="sm" />
          {/* Recency dot */}
          <span className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-bg bg-${recency}`} />
        </div>

        <div className="flex-1 min-w-0">
          {/* Name — serif, it's content */}
          <div className="flex items-center gap-2">
            <span className="text-[15px] font-serif font-[500] text-text leading-tight truncate">{person.name}</span>
            <ChevronRight className="w-3 h-3 text-text-quaternary opacity-0 group-hover:opacity-100 transition-opacity shrink-0" style={{ transitionDuration: 'var(--duration-instant)' }} />
          </div>

          {/* Channels row */}
          {channels.length > 0 && (
            <div className="flex items-center gap-1.5 mt-1.5">
              {channels.slice(0, 4).map(ch => (
                <span key={ch} className="inline-flex items-center gap-1 text-[10px] text-text-quaternary">
                  {channelIcon(ch)}
                  <span className="capitalize">{ch}</span>
                </span>
              ))}
            </div>
          )}

          {/* Tags — subtle */}
          {person.tags && person.tags.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {person.tags.slice(0, 3).map(t => (
                <Tag key={t} label={t} color="gray" size="sm" />
              ))}
            </div>
          )}
        </div>

        {/* Last contact timestamp */}
        <div className="text-right shrink-0 mt-0.5">
          <span className="text-[10px] text-text-quaternary tabular-nums">{timeAgo(person.last_interaction)}</span>
        </div>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Person Detail Panel
// ---------------------------------------------------------------------------

function PersonDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: person, isLoading } = usePerson(id);

  if (isLoading || !person) {
    return (
      <div className="p-6 space-y-4">
        <div className="flex items-center gap-4">
          <Skeleton className="h-14 w-14 rounded-full" />
          <div className="space-y-2 flex-1">
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-3 w-24" />
          </div>
        </div>
        <SkeletonRows count={6} />
      </div>
    );
  }

  const channels = person.channels ? Object.entries(person.channels) : [];
  const recency = recencyColor(person.last_interaction);

  return (
    <div className="overflow-y-auto h-full">
      {/* Header */}
      <div className="px-6 pt-6 pb-5 border-b border-border">
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-start gap-4">
            <div className="relative">
              <Initials name={person.name} size="lg" />
              <span className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-bg-panel bg-${recency}`} />
            </div>
            <div>
              {/* Name — serif, large */}
              <h2 className="text-[22px] font-serif font-[600] text-text tracking-[-0.015em] leading-tight">{person.name}</h2>
              {person.aliases && person.aliases.length > 0 && (
                <p className="text-[11px] text-text-quaternary mt-1">also known as {person.aliases.join(', ')}</p>
              )}
              <span className="inline-flex items-center gap-1.5 mt-2 text-[10px] text-text-quaternary">
                Last contact {timeAgo(person.last_interaction)}
              </span>
            </div>
          </div>
          <button onClick={onClose} className="lg:hidden w-8 h-8 flex items-center justify-center rounded-sm hover:bg-hover text-text-tertiary cursor-pointer transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="px-6 py-5 space-y-6">
        {/* Channels */}
        {channels.length > 0 && (
          <div>
            <SectionHeader label="Channels" />
            <div className="space-y-1.5">
              {channels.map(([ch, val]) => (
                <div key={ch} className="flex items-center gap-3 py-2 px-3 rounded-[5px] bg-bg-secondary/50 border border-border transition-colors hover:bg-bg-secondary cursor-default" style={{ transitionDuration: 'var(--duration-instant)' }}>
                  <span className="text-text-quaternary">{channelIcon(ch)}</span>
                  <span className="text-[12px] font-[510] text-text-secondary capitalize flex-1">{ch}</span>
                  <span className="text-[12px] text-text-tertiary font-mono truncate max-w-[180px]">{val}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Tags */}
        {person.tags && person.tags.length > 0 && (
          <div>
            <SectionHeader label="Tags" />
            <div className="flex flex-wrap gap-1.5">
              {person.tags.map(t => <Tag key={t} label={t} color="gray" />)}
            </div>
          </div>
        )}

        {/* Notes */}
        {person.notes && (
          <div>
            <SectionHeader label="Notes" />
            <p className="text-[13px] font-serif text-text-tertiary leading-[1.7]">{person.notes}</p>
          </div>
        )}

        {/* Recent Interactions */}
        {person.interactions && person.interactions.length > 0 && (
          <div>
            <SectionHeader label="Recent Interactions" count={person.interactions.length} />
            <div className="space-y-1">
              {person.interactions.slice(0, 20).map((ix: InteractionSchema) => {
                const color = channelTagColor[ix.channel.toLowerCase()] || 'gray';
                return (
                  <div
                    key={ix.id}
                    className="flex items-start gap-3 py-2.5 px-3 rounded-[5px] transition-colors hover:bg-hover"
                    style={{ transitionDuration: 'var(--duration-instant)' }}
                  >
                    <span className="text-text-quaternary mt-0.5 shrink-0">
                      {channelIcon(ix.channel)}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-0.5">
                        <Tag label={ix.channel} color={color} size="sm" />
                        <Tag label={ix.direction} color={ix.direction === 'inbound' ? 'blue' : 'green'} size="sm" />
                        <span className="text-[10px] text-text-quaternary tabular-nums ml-auto shrink-0">{timeAgo(ix.timestamp)}</span>
                      </div>
                      {ix.summary && <p className="text-[12px] font-serif text-text-tertiary leading-[1.6] line-clamp-2 mt-0.5">{ix.summary}</p>}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Surfaces Panel — people who need attention
// ---------------------------------------------------------------------------

function SurfacesPanel() {
  const { data, isLoading } = usePersonSurfaces();

  if (isLoading) return <div className="px-6 py-4"><SkeletonRows count={4} /></div>;

  if (!data || data.items.length === 0) {
    return (
      <EmptyState
        icon={<Users />}
        title="No one surfaced right now"
        description="People who need your attention will appear here based on interaction patterns."
      />
    );
  }

  return (
    <div className="px-6 py-4 space-y-2">
      {data.items.map((item: PersonSurfaceItem, i: number) => (
        <div
          key={i}
          className="flex items-start gap-3.5 p-4 rounded-[7px] bg-bg-secondary border border-border-secondary transition-all hover:border-border-tertiary hover:bg-bg-tertiary cursor-pointer"
          style={{ transitionDuration: 'var(--duration-fast)' }}
        >
          <div className="relative mt-0.5">
            <Initials name={item.person.name} size="sm" />
            <span className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-bg-secondary bg-accent" />
          </div>
          <div className="flex-1 min-w-0">
            <span className="text-[14px] font-serif font-[500] text-text block truncate leading-tight">{item.person.name}</span>
            <span className="text-[12px] font-serif text-text-tertiary leading-[1.5] mt-1 block">{item.reason}</span>
            {item.score !== undefined && (
              <div className="flex items-center gap-2 mt-2">
                <div className="w-16 h-1 bg-bg-quaternary rounded-full overflow-hidden">
                  <div className="h-full bg-accent rounded-full transition-all" style={{ width: `${Math.min(100, item.score * 100)}%`, transitionDuration: 'var(--duration-normal)' }} />
                </div>
                <span className="text-[10px] text-text-quaternary tabular-nums">{Math.round(item.score * 100)}%</span>
              </div>
            )}
          </div>
          <ArrowUpRight className="w-3.5 h-3.5 text-text-quaternary shrink-0 mt-1" />
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main People Page
// ---------------------------------------------------------------------------

export default function PeoplePage() {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<'list' | 'surfaces'>('list');
  const { data, isLoading, isError } = usePeople(searchQuery || undefined);
  const people = data?.people ?? [];

  // Sort: most recently interacted first
  const sorted = useMemo(() =>
    [...people].sort((a, b) => {
      if (!a.last_interaction) return 1;
      if (!b.last_interaction) return -1;
      return new Date(b.last_interaction).getTime() - new Date(a.last_interaction).getTime();
    }),
    [people]
  );

  return (
    <div className="flex flex-col h-full overflow-hidden bg-bg">
      {/* Header */}
      <div className="shrink-0 px-6 py-5 border-b border-border">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-4">
            <h1 className="text-[20px] font-serif font-[600] text-text tracking-[-0.015em]">People</h1>
            {data && (
              <span className="text-[11px] text-text-quaternary tabular-nums mt-1">{data.total} contacts</span>
            )}
          </div>
          <TabBar
            tabs={[
              { id: 'list', label: 'Contacts' },
              { id: 'surfaces', label: 'Surfaces' },
            ]}
            active={tab}
            onChange={(id) => setTab(id as 'list' | 'surfaces')}
          />
        </div>
        {tab === 'list' && (
          <div className="flex items-center gap-2.5 px-3.5 py-2.5 rounded-[5px] bg-bg-secondary border border-border-secondary transition-colors focus-within:border-border-tertiary" style={{ transitionDuration: 'var(--duration-fast)' }}>
            <Search className="w-4 h-4 text-text-quaternary shrink-0" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search people..."
              className="flex-1 bg-transparent text-[13px] text-text placeholder:text-text-quaternary outline-none"
            />
            {searchQuery && (
              <button onClick={() => setSearchQuery('')} className="p-1 rounded-xs hover:bg-hover transition-colors cursor-pointer" style={{ transitionDuration: 'var(--duration-instant)' }}>
                <X className="w-3.5 h-3.5 text-text-quaternary" />
              </button>
            )}
          </div>
        )}
      </div>

      {isError && <div className="px-6 pt-4"><ErrorBanner /></div>}

      {/* Content */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {tab === 'surfaces' ? (
          <div className="flex-1 overflow-y-auto">
            <SurfacesPanel />
          </div>
        ) : (
          <>
            {/* Contact list */}
            <div className="flex-1 overflow-y-auto">
              {isLoading ? (
                <div className="px-6 py-4"><SkeletonRows count={8} /></div>
              ) : sorted.length === 0 ? (
                <EmptyState
                  icon={<Users />}
                  title={searchQuery ? 'No one matched your search' : 'Your people directory is empty'}
                  description={searchQuery ? 'Try a different name or tag.' : 'People will appear here as you interact across channels.'}
                />
              ) : (
                <div>
                  {sorted.map((p) => (
                    <PersonCard
                      key={p.id}
                      person={p}
                      active={selectedId === p.id}
                      onClick={() => setSelectedId(p.id)}
                    />
                  ))}
                </div>
              )}
            </div>

            {/* Detail panel */}
            {selectedId && (
              <div
                className="
                  w-full lg:w-[420px] shrink-0
                  border-t lg:border-t-0 lg:border-l border-border
                  bg-bg-panel
                  fixed inset-0 lg:static z-50 lg:z-auto
                "
              >
                <PersonDetail id={selectedId} onClose={() => setSelectedId(null)} />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
