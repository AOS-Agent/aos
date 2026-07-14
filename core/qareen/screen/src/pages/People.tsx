import { useState, useMemo } from 'react';
import { useRegisterPageActions, type PageAction } from '@/hooks/usePageActions';
import {
  Search, Users, X, MessageCircle, Phone, Mail, Hash, AtSign,
  Star, Building2, ArrowUpRight,
  MessageSquare, ListTodo, Bell, TrendingUp, TrendingDown, Minus,
  Cake, Handshake, Link2, Download, Check, Loader2, Smartphone, Globe,
  Send, ChevronDown, ChevronUp, ShieldCheck,
} from 'lucide-react';
import { usePeople, usePerson, usePersonSurfaces, usePersonMessages, useSendMessage, useRecentActivity, useRelationshipGraph, usePersonClassification } from '@/hooks/usePeople';
import type { RecentActivityItem, GraphNode, GraphEdge } from '@/hooks/usePeople';
import { EmptyState } from '@/components/primitives/EmptyState';
import { Tag, type TagColor } from '@/components/primitives/Tag';
import { SectionHeader } from '@/components/primitives/SectionHeader';
import { Skeleton, SkeletonRows } from '@/components/primitives/Skeleton';
import { ErrorBanner } from '@/components/primitives/ErrorBanner';
import CircleBrowser from '@/components/people/CircleBrowser';
import GraphExplorer from '@/components/people/GraphExplorer';
import FamilyTree from '@/components/people/FamilyTree';
import OrgChart from '@/components/people/OrgChart';
import HygienePanel, { HygieneBadge } from '@/components/people/HygienePanel';
// Phase 4 — people intelligence
import TierStrip from '@/components/people/TierStrip';
import PersonProfilePanel from '@/components/people/PersonProfilePanel';
import ClassificationCorrector from '@/components/people/ClassificationCorrector';
import type { PersonResponse, PersonSurfaceItem, RelationshipSchema, ChannelMessage, ChannelPresence } from '@/lib/types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function timeAgo(iso: string | undefined | null): string {
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

function recencyColor(iso: string | undefined | null): 'green' | 'yellow' | 'orange' | 'gray' {
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

const RECENCY_BG: Record<string, string> = {
  green: 'bg-green', yellow: 'bg-yellow', orange: 'bg-orange', gray: 'bg-text-quaternary',
};

const CHANNEL_DOT_BG: Record<string, string> = {
  green: 'bg-green', blue: 'bg-blue', purple: 'bg-purple', teal: 'bg-teal',
  orange: 'bg-orange', yellow: 'bg-yellow', gray: 'bg-text-quaternary',
};

const channelTagColor: Record<string, TagColor> = {
  whatsapp: 'green', telegram: 'blue', email: 'purple', slack: 'teal',
  sms: 'orange', phone: 'yellow', imessage: 'blue',
};

function trendIcon(trend: string | undefined) {
  if (trend === 'growing') return <TrendingUp className="w-3 h-3" />;
  if (trend === 'drifting') return <TrendingDown className="w-3 h-3" />;
  return <Minus className="w-3 h-3" />;
}

function trendColor(trend: string | undefined): TagColor {
  if (trend === 'growing') return 'green';
  if (trend === 'drifting') return 'orange';
  return 'gray';
}

function importanceLabel(imp: number): string {
  if (imp === 1) return 'Inner circle';
  if (imp === 2) return 'Key';
  if (imp === 3) return 'Regular';
  return 'Acquaintance';
}

function Initials({ name, size = 'md' }: { name: string; size?: 'sm' | 'md' | 'lg' }) {
  const clean = name.replace(/[^\p{L}\p{N}\s]/gu, '').trim();
  const letters = clean.split(/\s+/).map(w => w[0] || '').join('').slice(0, 2).toUpperCase();
  const dims = size === 'lg' ? 'w-14 h-14 text-[18px]' : size === 'md' ? 'w-10 h-10 text-[14px]' : 'w-8 h-8 text-[12px]';
  return (
    <div className={`${dims} rounded-full bg-bg-tertiary border border-border-secondary flex items-center justify-center font-[590] text-text-tertiary shrink-0 transition-colors`} style={{ transitionDuration: 'var(--duration-instant)' }}>
      {letters || '?'}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inner Circle — horizontal avatar row
// ---------------------------------------------------------------------------

function InnerCircleRow({ people, onSelect, selectedId }: {
  people: PersonResponse[];
  onSelect: (id: string) => void;
  selectedId: string | null;
}) {
  const inner = useMemo(() =>
    people.filter(p => p.importance <= 2)
      .sort((a, b) => {
        if (!a.last_contact) return 1;
        if (!b.last_contact) return -1;
        return new Date(b.last_contact).getTime() - new Date(a.last_contact).getTime();
      }),
    [people]
  );

  if (inner.length === 0) return null;

  return (
    <div className="mb-8">
      <div className="flex items-center gap-4 overflow-x-auto scrollbar-none pb-2">
        {inner.map(p => {
          const recency = recencyColor(p.last_contact);
          const isActive = selectedId === p.id;
          return (
            <button
              key={p.id}
              onClick={() => onSelect(p.id)}
              className={`flex flex-col items-center gap-1.5 min-w-[56px] cursor-pointer group transition-all ${isActive ? 'scale-105' : ''}`}
              style={{ transitionDuration: 'var(--duration-fast)' }}
            >
              <div className="relative">
                <div className={`w-12 h-12 rounded-full bg-bg-tertiary border-2 flex items-center justify-center font-[590] text-[14px] text-text-tertiary transition-colors ${
                  isActive ? 'border-accent' : 'border-border-secondary group-hover:border-border-tertiary'
                }`} style={{ transitionDuration: 'var(--duration-instant)' }}>
                  {p.name.replace(/[^\p{L}\p{N}\s]/gu, '').trim().split(/\s+/).map(w => w[0] || '').join('').slice(0, 2).toUpperCase() || '?'}
                </div>
                <span className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-bg ${RECENCY_BG[recency]}`} />
              </div>
              <span className="text-[10px] text-text-tertiary truncate max-w-[56px] group-hover:text-text-secondary transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
                {p.name.split(' ')[0]}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Recent Activity Feed
// ---------------------------------------------------------------------------

function ActivityFeed({ onSelect }: { onSelect: (id: string) => void }) {
  const { data, isLoading } = useRecentActivity(14, 20);
  const items = data?.items ?? [];

  // Group by date — must be before any early returns (hooks order)
  const grouped = useMemo(() => {
    const groups: Record<string, RecentActivityItem[]> = {};
    for (const item of items) {
      const date = item.occurred_at ? item.occurred_at.slice(0, 10) : 'unknown';
      (groups[date] ??= []).push(item);
    }
    return Object.entries(groups).sort(([a], [b]) => b.localeCompare(a));
  }, [items]);

  if (isLoading) return <SkeletonRows count={4} />;
  if (items.length === 0) return null;

  function dateLabel(iso: string): string {
    const d = new Date(iso);
    const now = new Date();
    const diff = Math.floor((now.getTime() - d.getTime()) / 86400000);
    if (diff === 0) return 'Today';
    if (diff === 1) return 'Yesterday';
    if (diff < 7) return d.toLocaleDateString('en', { weekday: 'long' });
    return d.toLocaleDateString('en', { month: 'short', day: 'numeric' });
  }

  return (
    <div className="mb-8">
      <h2 className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary mb-3 px-1">Recent</h2>
      <div className="space-y-5">
        {grouped.map(([date, items]) => (
          <div key={date}>
            <span className="text-[10px] text-text-quaternary px-1 mb-1.5 block tabular-nums">{dateLabel(date)}</span>
            <div className="space-y-px">
              {items.map((item, i) => {
                const arrow = item.direction === 'outbound' ? '→' : item.direction === 'both' ? '↔' : '←';
                const color = channelTagColor[item.channel] || 'gray';
                return (
                  <button
                    key={`${item.person_id}-${i}`}
                    onClick={() => onSelect(item.person_id)}
                    className="group w-full text-left flex items-center gap-3 px-3 py-2 rounded-[5px] hover:bg-hover transition-all cursor-pointer"
                    style={{ transitionDuration: 'var(--duration-instant)' }}
                  >
                    <Initials name={item.person_name} size="sm" />
                    <div className="flex-1 min-w-0">
                      <span className="text-[13px] font-[510] text-text-secondary group-hover:text-text transition-colors truncate block" style={{ transitionDuration: 'var(--duration-instant)' }}>
                        {item.person_name}
                      </span>
                      {item.organization && (
                        <span className="text-[11px] text-text-quaternary">{item.organization}</span>
                      )}
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <Tag label={item.channel} color={color} size="sm" />
                      <span className="text-[11px] text-text-quaternary tabular-nums">
                        {arrow} {item.msg_count} msg{item.msg_count !== 1 ? 's' : ''}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Needs Attention
// ---------------------------------------------------------------------------

function NeedsAttention({ onSelect }: { onSelect: (id: string) => void }) {
  const { data, isLoading } = usePersonSurfaces();

  if (isLoading) return <SkeletonRows count={3} />;

  const surfaces = data?.surfaces ?? [];
  if (surfaces.length === 0) return null;

  return (
    <div className="mb-8">
      <h2 className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary mb-3 px-1">Needs attention</h2>
      <div className="space-y-1">
        {surfaces.slice(0, 6).map((item, i) => (
          <button
            key={i}
            onClick={() => onSelect(item.person.id)}
            className="group w-full text-left flex items-center gap-3.5 px-3 py-2.5 rounded-[7px] hover:bg-hover transition-all cursor-pointer"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            <div className="relative shrink-0">
              <Initials name={item.person.name} size="sm" />
              <span className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-bg bg-accent" />
            </div>
            <div className="flex-1 min-w-0">
              <span className="text-[13px] font-[510] text-text-secondary group-hover:text-text transition-colors truncate block" style={{ transitionDuration: 'var(--duration-instant)' }}>
                {item.person.name}
              </span>
              <span className="text-[12px] text-text-tertiary leading-[1.5]">{item.suggested_action || item.reason}</span>
            </div>
            <ArrowUpRight className="w-3.5 h-3.5 text-text-quaternary shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" style={{ transitionDuration: 'var(--duration-instant)' }} />
          </button>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// People Graph — family + connections canvas
// ---------------------------------------------------------------------------

function PeopleGraph({ onSelect }: { onSelect: (id: string) => void }) {
  const { data, isLoading } = useRelationshipGraph();

  if (isLoading) return <Skeleton className="h-48 w-full rounded-[7px]" />;

  const nodes = data?.nodes ?? [];
  const edges = data?.edges ?? [];

  if (edges.length === 0) return null;

  // Group edges by type
  const familyEdges = edges.filter(e => e.type === 'family');
  const otherEdges = edges.filter(e => e.type !== 'family');
  const nodeMap = new Map(nodes.map(n => [n.id, n]));

  return (
    <div className="mb-8">
      <h2 className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary mb-3 px-1">Connections</h2>

      {/* Family */}
      {familyEdges.length > 0 && (
        <div className="mb-4">
          <span className="text-[10px] text-text-quaternary px-1 mb-2 block">Family</span>
          <div className="flex flex-wrap gap-2">
            {familyEdges.map((edge, i) => {
              const person = nodeMap.get(edge.target);
              if (!person) return null;
              return (
                <button
                  key={i}
                  onClick={() => onSelect(person.id)}
                  className="flex items-center gap-2 px-3 py-2 rounded-[7px] bg-bg-secondary/50 border border-border hover:border-border-tertiary transition-all cursor-pointer"
                  style={{ transitionDuration: 'var(--duration-instant)' }}
                >
                  <Initials name={person.name} size="sm" />
                  <div className="text-left">
                    <span className="text-[12px] font-[510] text-text-secondary block">{person.name}</span>
                    <span className="text-[10px] text-text-quaternary capitalize">{edge.subtype || edge.type}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Other connections */}
      {otherEdges.length > 0 && (
        <div>
          <span className="text-[10px] text-text-quaternary px-1 mb-2 block">Other connections</span>
          <div className="flex flex-wrap gap-2">
            {otherEdges.map((edge, i) => {
              const person = nodeMap.get(edge.target) || nodeMap.get(edge.source);
              if (!person) return null;
              return (
                <button
                  key={i}
                  onClick={() => onSelect(person.id)}
                  className="flex items-center gap-2 px-2.5 py-1.5 rounded-[5px] bg-bg-secondary/30 border border-border hover:bg-hover transition-all cursor-pointer"
                  style={{ transitionDuration: 'var(--duration-instant)' }}
                >
                  <span className="text-[11px] text-text-secondary">{person.name}</span>
                  <Tag label={edge.subtype || edge.type} color="purple" size="sm" />
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Directory — collapsible search + tier-grouped list
// ---------------------------------------------------------------------------

function Directory({ people, isLoading, onSelect, selectedId }: {
  people: PersonResponse[];
  isLoading: boolean;
  onSelect: (id: string) => void;
  selectedId: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');

  const filtered = useMemo(() => {
    if (!query) return people;
    const q = query.toLowerCase();
    return people.filter(p =>
      p.name.toLowerCase().includes(q) ||
      (p.organization || '').toLowerCase().includes(q) ||
      p.tags.some(t => t.toLowerCase().includes(q))
    );
  }, [people, query]);

  const grouped = useMemo(() => {
    const tiers: Record<string, PersonResponse[]> = {};
    for (const p of filtered) {
      const label = importanceLabel(p.importance);
      (tiers[label] ??= []).push(p);
    }
    for (const arr of Object.values(tiers)) {
      arr.sort((a, b) => {
        if (!a.last_contact) return 1;
        if (!b.last_contact) return -1;
        return new Date(b.last_contact).getTime() - new Date(a.last_contact).getTime();
      });
    }
    return Object.entries(tiers).filter(([, arr]) => arr.length > 0);
  }, [filtered]);

  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 w-full px-1 mb-3 cursor-pointer"
      >
        <h2 className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">Directory</h2>
        <span className="text-[10px] text-text-quaternary tabular-nums">{people.length}</span>
        <div className="flex-1" />
        {open ? <ChevronUp className="w-3.5 h-3.5 text-text-quaternary" /> : <ChevronDown className="w-3.5 h-3.5 text-text-quaternary" />}
      </button>

      {open && (
        <div>
          <div className="relative mb-4">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-quaternary" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search people..."
              className="w-full h-8 pl-8 pr-3 bg-bg-secondary border border-border rounded-sm text-[12px] text-text placeholder:text-text-quaternary focus:border-accent/40 focus:outline-none transition-colors"
              style={{ transitionDuration: 'var(--duration-fast)' }}
              autoFocus
            />
            {query && (
              <button onClick={() => setQuery('')} className="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 rounded-xs hover:bg-hover transition-colors cursor-pointer" style={{ transitionDuration: 'var(--duration-instant)' }}>
                <X className="w-3 h-3 text-text-quaternary" />
              </button>
            )}
          </div>

          {isLoading ? (
            <SkeletonRows count={6} />
          ) : filtered.length === 0 ? (
            <EmptyState icon={<Search />} title="No one matched" description="Try a different name, tag, or organization." />
          ) : (
            <div className="space-y-5">
              {grouped.map(([tier, persons]) => (
                <div key={tier}>
                  <div className="flex items-center gap-2 mb-1.5 px-1">
                    <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">{tier}</span>
                    <span className="text-[10px] text-text-quaternary tabular-nums">{persons.length}</span>
                  </div>
                  <div className="space-y-px">
                    {persons.map(p => {
                      const recency = recencyColor(p.last_contact);
                      const channels = p.channels ? Object.keys(p.channels) : [];
                      return (
                        <button
                          key={p.id}
                          onClick={() => onSelect(p.id)}
                          className={`group w-full text-left flex items-center gap-3 px-3 py-2 rounded-[5px] transition-all cursor-pointer ${selectedId === p.id ? 'bg-selected' : 'hover:bg-hover'}`}
                          style={{ transitionDuration: 'var(--duration-instant)' }}
                        >
                          <div className="relative shrink-0">
                            <Initials name={p.name} size="sm" />
                            <span className={`absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full border-2 border-bg ${RECENCY_BG[recency]}`} />
                          </div>
                          <div className="flex-1 min-w-0">
                            <span className="text-[12px] font-[510] text-text-secondary truncate block group-hover:text-text transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>{p.name}</span>
                            {(p.organization || p.role) && (
                              <span className="text-[11px] text-text-quaternary truncate block">{[p.role, p.organization].filter(Boolean).join(' · ')}</span>
                            )}
                          </div>
                          <div className="flex items-center gap-2 shrink-0">
                            {channels.slice(0, 2).map(ch => (
                              <span key={ch} className="text-text-quaternary">{channelIcon(ch)}</span>
                            ))}
                            <span className="text-[10px] text-text-quaternary tabular-nums">{timeAgo(p.last_contact)}</span>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Person Detail Panel — slide-over
// ---------------------------------------------------------------------------

function PersonDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: person, isLoading } = usePerson(id);
  const { data: msgData } = usePersonMessages(id);
  const sendMutation = useSendMessage();
  const [showCompose, setShowCompose] = useState(false);
  const [composeChannel, setComposeChannel] = useState<string>('');
  const [composeText, setComposeText] = useState('');
  const [detailTab, setDetailTab] = useState<'messages' | 'info'>('messages');
  // Phase 4 — classification corrector toggle
  const [showCorrector, setShowCorrector] = useState(false);
  const { data: classification } = usePersonClassification(id);

  if (isLoading || !person) {
    return (
      <div className="p-6 space-y-4">
        <div className="flex items-center gap-4">
          <Skeleton className="h-14 w-14 rounded-full" />
          <div className="space-y-2 flex-1"><Skeleton className="h-5 w-40" /><Skeleton className="h-3 w-24" /></div>
        </div>
        <SkeletonRows count={6} />
      </div>
    );
  }

  const channels = person.channels ? Object.entries(person.channels) : [];
  const recency = recencyColor(person.last_contact);
  const presence = person.presence ?? msgData?.presence ?? [];
  const messages = msgData?.messages ?? [];

  const handleSend = async () => {
    if (!composeText.trim() || !composeChannel) return;
    await sendMutation.mutateAsync({ personId: id, channel: composeChannel, text: composeText });
    setComposeText('');
    setShowCompose(false);
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="shrink-0 px-6 pt-6 pb-4 border-b border-border">
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-start gap-4">
            <div className="relative">
              <Initials name={person.name} size="lg" />
              <span className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-bg-panel ${RECENCY_BG[recency]}`} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-[22px] font-[600] text-text tracking-[-0.015em] leading-tight">{person.name}</h2>
                {person.importance <= 2 && <Star className="w-4 h-4 text-accent" />}
              </div>
              {(person.organization || person.role) && (
                <p className="text-[12px] text-text-secondary mt-1 flex items-center gap-1.5">
                  {person.role && <span>{person.role}</span>}
                  {person.role && person.organization && <span className="text-text-quaternary">at</span>}
                  {person.organization && <span className="inline-flex items-center gap-1"><Building2 className="w-3 h-3 text-text-quaternary" />{person.organization}</span>}
                </p>
              )}
              {person.relationship_trend ? (
                <div className="flex items-center gap-2 mt-1.5">
                  <Tag label={person.relationship_trend} color={trendColor(person.relationship_trend)} size="sm" icon={trendIcon(person.relationship_trend)} />
                  <span className="text-[10px] text-text-quaternary">{timeAgo(person.last_contact)}</span>
                </div>
              ) : person.last_contact ? (
                <span className="text-[10px] text-text-quaternary mt-1.5 block">{timeAgo(person.last_contact)}</span>
              ) : null}
            </div>
          </div>
          <button onClick={onClose} className="w-8 h-8 flex items-center justify-center rounded-sm hover:bg-hover text-text-tertiary cursor-pointer transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Channel presence */}
        {presence.length > 0 && (
          <div className="flex items-center gap-2 mb-3">
            {presence.map((p: ChannelPresence) => {
              const color = channelTagColor[p.channel] || 'gray';
              const dotBg = CHANNEL_DOT_BG[color] || 'bg-text-quaternary';
              return (
                <div key={p.channel} className="flex items-center gap-1.5 px-2 py-1 rounded-[5px] bg-bg-secondary/50 border border-border">
                  <span className={`w-1.5 h-1.5 rounded-full ${dotBg}`} />
                  <span className="text-[10px] text-text-tertiary capitalize">{p.channel}</span>
                  <span className="text-[10px] text-text-quaternary tabular-nums">{timeAgo(p.last_message_at)}</span>
                </div>
              );
            })}
          </div>
        )}

        {/* Actions + tabs */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <button onClick={() => { setShowCompose(!showCompose); if (!composeChannel && channels.length > 0) setComposeChannel(channels[0][0]); }}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-[5px] border text-[11px] font-[510] transition-colors cursor-pointer ${showCompose ? 'bg-accent text-bg border-accent' : 'bg-bg-secondary border-border-secondary text-text-tertiary hover:text-text-secondary hover:bg-bg-tertiary'}`}
              style={{ transitionDuration: 'var(--duration-instant)' }}
            ><MessageSquare className="w-3.5 h-3.5" />Message</button>
          </div>
          <div className="flex items-center gap-1">
            <button onClick={() => setDetailTab('messages')} className={`px-2 py-1 rounded-[3px] text-[10px] font-[510] transition-colors cursor-pointer ${detailTab === 'messages' ? 'bg-bg-tertiary text-text-secondary' : 'text-text-quaternary hover:text-text-tertiary'}`} style={{ transitionDuration: 'var(--duration-instant)' }}>Messages</button>
            <button onClick={() => setDetailTab('info')} className={`px-2 py-1 rounded-[3px] text-[10px] font-[510] transition-colors cursor-pointer ${detailTab === 'info' ? 'bg-bg-tertiary text-text-secondary' : 'text-text-quaternary hover:text-text-tertiary'}`} style={{ transitionDuration: 'var(--duration-instant)' }}>Info</button>
          </div>
        </div>
      </div>

      {/* Compose */}
      {showCompose && (
        <div className="shrink-0 px-6 py-3 border-b border-border bg-bg-secondary/50">
          <div className="flex items-center gap-2 mb-2">
            {channels.map(([ch]) => (
              <button key={ch} onClick={() => setComposeChannel(ch)}
                className={`px-2 py-0.5 rounded-[3px] text-[10px] font-[510] capitalize transition-colors cursor-pointer ${composeChannel === ch ? 'bg-accent text-bg' : 'bg-bg-tertiary text-text-tertiary hover:text-text-secondary'}`}
                style={{ transitionDuration: 'var(--duration-instant)' }}
              >{ch}</button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <input type="text" value={composeText} onChange={(e) => setComposeText(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
              placeholder={`Message via ${composeChannel}...`}
              className="flex-1 bg-bg-secondary border border-border-secondary rounded-[5px] px-3 py-2 text-[13px] text-text placeholder:text-text-quaternary outline-none focus:border-border-tertiary transition-colors"
              style={{ transitionDuration: 'var(--duration-fast)' }} autoFocus />
            <button onClick={handleSend} disabled={!composeText.trim() || sendMutation.isPending}
              className="flex items-center justify-center w-8 h-8 rounded-[5px] bg-accent text-bg hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-50"
              style={{ transitionDuration: 'var(--duration-instant)' }}
            >{sendMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}</button>
          </div>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {detailTab === 'messages' ? (
          <div className="px-6 py-4">
            {messages.length === 0 ? (
              <div className="text-center py-8">
                <MessageCircle className="w-8 h-8 text-text-quaternary mx-auto mb-3" />
                <p className="text-[13px] text-text-tertiary">No messages with {person.name} yet</p>
                <p className="text-[11px] text-text-quaternary mt-1">Messages will appear as you communicate across channels.</p>
              </div>
            ) : (
              <div className="space-y-1">
                {messages.map((msg: ChannelMessage) => {
                  const color = channelTagColor[msg.channel?.toLowerCase()] || 'gray';
                  return (
                    <div key={msg.id} className={`flex flex-col py-2.5 px-3 rounded-[5px] transition-colors hover:bg-hover ${msg.from_me ? 'items-end' : 'items-start'}`} style={{ transitionDuration: 'var(--duration-instant)' }}>
                      <div className="flex items-center gap-2 mb-0.5">
                        {msg.from_me ? <span className="text-[10px] font-[510] text-accent">you</span> : <span className="text-[10px] font-[510] text-text-tertiary">{msg.sender}</span>}
                        <Tag label={msg.channel} color={color} size="sm" />
                        <span className="text-[10px] text-text-quaternary tabular-nums">{timeAgo(msg.timestamp)}</span>
                      </div>
                      <div className={`max-w-[85%] px-3 py-2 rounded-[7px] ${msg.from_me ? 'bg-accent-subtle' : 'bg-bg-secondary border border-border'}`}>
                        {msg.text ? <p className="text-[13px] text-text-secondary leading-[1.6] break-words">{msg.text}</p> : <p className="text-[11px] text-text-quaternary italic">{msg.media_type || 'media'}</p>}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        ) : (
          <div className="px-6 py-5 space-y-6">
            {/* Phase 4 — compiled profile + classification */}
            <PersonProfilePanel personId={id} />

            {/* Phase 4 — correct classification (collapsible) */}
            <div>
              <button
                onClick={() => setShowCorrector(v => !v)}
                className="text-[11px] font-[510] text-text-tertiary hover:text-text-secondary cursor-pointer transition-colors"
                style={{ transitionDuration: 'var(--duration-instant)' }}
              >
                {showCorrector ? 'Cancel correction' : 'Correct classification…'}
              </button>
              {showCorrector && (
                <div className="mt-3">
                  <ClassificationCorrector
                    personId={id}
                    currentTier={(classification as any)?.tier || 'unknown'}
                    currentTags={(classification as any)?.context_tags || []}
                    onSaved={() => setShowCorrector(false)}
                  />
                </div>
              )}
            </div>

            {channels.length > 0 && (
              <div>
                <SectionHeader label="Channels" />
                <div className="space-y-1.5">
                  {channels.map(([ch, val]) => {
                    const color = channelTagColor[ch.toLowerCase()] || 'gray';
                    return (
                      <div key={ch} className="flex items-center gap-3 py-2 px-3 rounded-[5px] bg-bg-secondary/50 border border-border">
                        <span className="text-text-quaternary">{channelIcon(ch)}</span>
                        <Tag label={ch} color={color} size="sm" />
                        <span className="text-[12px] text-text-tertiary font-mono truncate max-w-[180px] flex-1 text-right">{val}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            {person.relationships && person.relationships.length > 0 && (
              <div>
                <SectionHeader label="Relationships" count={person.relationships.length} />
                <div className="space-y-1.5">
                  {person.relationships.map((rel: RelationshipSchema, i: number) => (
                    <div key={i} className="flex items-center gap-3 py-2 px-3 rounded-[5px] bg-bg-secondary/50 border border-border">
                      <Link2 className="w-3 h-3 text-text-quaternary shrink-0" />
                      <Tag label={rel.link_type} color="purple" size="sm" />
                      <span className="text-[12px] text-text-secondary truncate">{rel.target_name || rel.target_id}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {person.tags && person.tags.length > 0 && (
              <div>
                <SectionHeader label="Tags" />
                <div className="flex flex-wrap gap-1.5">
                  {person.tags.map(t => <Tag key={t} label={t} color="gray" />)}
                </div>
              </div>
            )}
            {person.notes && (
              <div>
                <SectionHeader label="Notes" />
                <p className="text-[13px] text-text-tertiary leading-[1.7]">{person.notes}</p>
              </div>
            )}
            <div>
              <SectionHeader label="Details" />
              <div className="space-y-2">
                {person.birthday && <div className="flex items-center gap-2 text-[12px]"><Cake className="w-3.5 h-3.5 text-text-quaternary" /><span className="text-text-tertiary">Birthday:</span><span className="text-text-secondary">{person.birthday}</span></div>}
                {person.how_met && <div className="flex items-center gap-2 text-[12px]"><Handshake className="w-3.5 h-3.5 text-text-quaternary" /><span className="text-text-tertiary">Met via:</span><span className="text-text-secondary">{person.how_met}</span></div>}
                <div className="flex items-center gap-2 text-[12px]"><Star className="w-3.5 h-3.5 text-text-quaternary" /><span className="text-text-tertiary">Importance:</span><span className="text-text-secondary">{importanceLabel(person.importance)}</span></div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// View tabs
// ---------------------------------------------------------------------------

const VIEW_TABS = [
  { id: 'feed', label: 'Feed' },
  { id: 'graph', label: 'Graph' },
  { id: 'family', label: 'Family' },
  { id: 'circles', label: 'Circles' },
  { id: 'orgs', label: 'Orgs' },
];

// ---------------------------------------------------------------------------
// Main People Page — tabbed views
// ---------------------------------------------------------------------------

export default function PeoplePage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeView, setActiveView] = useState('feed');
  const [showHygiene, setShowHygiene] = useState(false);
  // Phase 4 — tier filter state (TierStrip click-to-filter)
  const [selectedTier, setSelectedTier] = useState<string | null>(null);
  const { data, isLoading, isError } = usePeople();
  const allPeople = data?.people ?? [];

  const pageActions: PageAction[] = useMemo(() => [
    {
      id: 'people.switch_view',
      label: 'Switch People view',
      category: 'navigate',
      params: [{ name: 'view', type: 'enum' as const, required: true, description: 'View to switch to', options: ['feed', 'graph', 'family', 'circles', 'orgs'] }],
      execute: ({ view }) => setActiveView(view as string),
    },
    {
      id: 'people.toggle_hygiene',
      label: 'Show data hygiene panel',
      category: 'toggle',
      execute: () => setShowHygiene(h => !h),
    },
  ], [])
  useRegisterPageActions(pageActions)

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[860px] mx-auto px-6 md:px-8 pt-10 pb-12">
        {isError && <div className="mb-6"><ErrorBanner /></div>}

        {/* Tab bar (glass pill) + hygiene badge */}
        <div className="flex items-center gap-3 mb-8">
          <div className="inline-flex items-center gap-1 h-9 rounded-full bg-[rgba(30,26,22,0.60)] backdrop-blur-xl border border-[rgba(255,245,235,0.06)] shadow-[0_2px_12px_rgba(0,0,0,0.3)] px-1">
            {VIEW_TABS.map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveView(tab.id)}
                className={`px-3.5 h-7 rounded-full text-[12px] font-[510] transition-colors cursor-pointer ${
                  activeView === tab.id
                    ? 'bg-bg-tertiary text-text shadow-[0_0_0_1px_rgba(255,245,235,0.06)]'
                    : 'text-text-quaternary hover:text-text-tertiary'
                }`}
                style={{ transitionDuration: 'var(--duration-instant)' }}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <div className="ml-auto">
            <HygieneBadge onClick={() => setShowHygiene(true)} />
          </div>
        </div>

        {/* Phase 4 — tier distribution strip with click-to-filter */}
        <TierStrip selectedTier={selectedTier} onSelectTier={setSelectedTier} />

        {/* Feed view — original content */}
        {activeView === 'feed' && (
          <>
            {/* Inner circle — horizontal avatar row */}
            {!isLoading && <InnerCircleRow people={allPeople} onSelect={setSelectedId} selectedId={selectedId} />}

            {/* Recent activity feed */}
            <ActivityFeed onSelect={setSelectedId} />

            {/* Needs attention */}
            <NeedsAttention onSelect={setSelectedId} />

            {/* People graph — family + connections */}
            <PeopleGraph onSelect={setSelectedId} />

            {/* Directory — collapsible */}
            {!isLoading && (
              <Directory people={allPeople} isLoading={isLoading} onSelect={setSelectedId} selectedId={selectedId} />
            )}
          </>
        )}

        {/* Graph view */}
        {activeView === 'graph' && (
          <GraphExplorer onSelect={setSelectedId} />
        )}

        {/* Family tree view */}
        {activeView === 'family' && (
          <FamilyTree onSelect={setSelectedId} />
        )}

        {/* Circles view */}
        {activeView === 'circles' && (
          <CircleBrowser onSelect={setSelectedId} />
        )}

        {/* Organizations view */}
        {activeView === 'orgs' && (
          <OrgChart onSelect={setSelectedId} />
        )}
      </div>

      {/* Hygiene panel slide-over */}
      {showHygiene && (
        <>
          <div
            className="fixed inset-0 z-40 bg-black/30 backdrop-blur-sm"
            onClick={() => setShowHygiene(false)}
            style={{ animation: 'peopleOverlayIn 180ms var(--ease-out)' }}
          />
          <div
            className="fixed top-0 right-0 bottom-0 z-50 w-full max-w-[480px] bg-bg-panel border-l border-border shadow-[var(--shadow-high)] flex flex-col"
            style={{ animation: 'peopleSlideIn 180ms var(--ease-out)' }}
          >
            <div className="shrink-0 flex items-center justify-between px-6 pt-6 pb-4 border-b border-border">
              <div className="flex items-center gap-2">
                <ShieldCheck className="w-4 h-4 text-accent" />
                <h2 className="text-[16px] font-[600] text-text tracking-[-0.01em]">Data hygiene</h2>
              </div>
              <button
                onClick={() => setShowHygiene(false)}
                className="w-8 h-8 flex items-center justify-center rounded-sm hover:bg-hover text-text-tertiary cursor-pointer transition-colors"
                style={{ transitionDuration: 'var(--duration-instant)' }}
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-6 py-5">
              <HygienePanel />
            </div>
          </div>
        </>
      )}

      {/* Detail slide-over */}
      {selectedId && (
        <>
          <div
            className="fixed inset-0 z-40 bg-black/30 backdrop-blur-sm"
            onClick={() => setSelectedId(null)}
            style={{ animation: 'peopleOverlayIn 180ms var(--ease-out)' }}
          />
          <div
            className="fixed top-0 right-0 bottom-0 z-50 w-full max-w-[440px] bg-bg-panel border-l border-border shadow-[var(--shadow-high)]"
            style={{ animation: 'peopleSlideIn 180ms var(--ease-out)' }}
          >
            <PersonDetail id={selectedId} onClose={() => setSelectedId(null)} />
          </div>
        </>
      )}

      <style>{`
        @keyframes peopleOverlayIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        @keyframes peopleSlideIn {
          from { transform: translateX(100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
