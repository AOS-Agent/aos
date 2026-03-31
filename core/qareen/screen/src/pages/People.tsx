import { useState } from 'react';
import { Search, Users, X, MessageCircle } from 'lucide-react';
import { usePeople, usePerson, usePersonSurfaces } from '@/hooks/usePeople';
import { EmptyState } from '@/components/primitives/EmptyState';
import { Tag } from '@/components/primitives/Tag';
import { SectionHeader } from '@/components/primitives/SectionHeader';
import { Skeleton } from '@/components/primitives/Skeleton';
import { SkeletonRows } from '@/components/primitives/Skeleton';
import { ErrorBanner } from '@/components/primitives/ErrorBanner';
import { TabBar } from '@/components/primitives/TabBar';
import { StatusDot } from '@/components/primitives/StatusDot';
import type { PersonResponse, PersonSurfaceItem, InteractionSchema } from '@/lib/types';

function timeAgo(iso: string | undefined): string {
  if (!iso) return 'never';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

function PersonRow({ person, active, onClick }: { person: PersonResponse; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`w-full flex items-center gap-3 px-4 py-3 text-left transition-colors ${active ? 'bg-active' : 'hover:bg-hover'}`} style={{ transitionDuration: 'var(--duration-instant)' }}>
      <div className="w-8 h-8 rounded-full bg-bg-tertiary flex items-center justify-center text-[12px] font-[590] text-text-secondary shrink-0">
        {person.name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()}
      </div>
      <div className="flex-1 min-w-0">
        <span className="text-[13px] font-[510] text-text-secondary block truncate">{person.name}</span>
        {person.tags && person.tags.length > 0 && <span className="text-[10px] text-text-quaternary truncate block">{person.tags.slice(0, 3).join(', ')}</span>}
      </div>
      <span className="text-[10px] text-text-quaternary shrink-0">{timeAgo(person.last_interaction)}</span>
    </button>
  );
}

function PersonDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: person, isLoading } = usePerson(id);
  if (isLoading || !person) return <div className="p-5 space-y-3"><Skeleton className="h-10 w-10 rounded-full" /><Skeleton className="h-5 w-40" /><SkeletonRows count={4} /></div>;

  return (
    <div className="p-5 overflow-y-auto">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-bg-tertiary flex items-center justify-center text-[14px] font-[590] text-text-secondary">
            {person.name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()}
          </div>
          <div>
            <h2 className="text-[17px] font-[650] text-text tracking-[-0.01em]">{person.name}</h2>
            {person.aliases && person.aliases.length > 0 && <p className="text-[11px] text-text-quaternary">{person.aliases.join(', ')}</p>}
          </div>
        </div>
        <button onClick={onClose} className="lg:hidden w-8 h-8 flex items-center justify-center rounded-sm hover:bg-hover text-text-tertiary"><X className="w-4 h-4" /></button>
      </div>
      {person.channels && Object.keys(person.channels).length > 0 && (
        <div className="mb-6"><SectionHeader label="Channels" /><div className="space-y-1">{Object.entries(person.channels).map(([ch, val]) => <div key={ch} className="flex items-center justify-between py-1"><span className="text-[11px] text-text-quaternary capitalize">{ch}</span><span className="text-[12px] text-text-secondary font-mono">{val}</span></div>)}</div></div>
      )}
      {person.tags && person.tags.length > 0 && <div className="mb-6"><SectionHeader label="Tags" /><div className="flex flex-wrap gap-1.5">{person.tags.map(t => <Tag key={t} label={t} color="gray" />)}</div></div>}
      {person.notes && <div className="mb-6"><SectionHeader label="Notes" /><p className="text-[12px] text-text-tertiary leading-relaxed">{person.notes}</p></div>}
      {person.interactions && person.interactions.length > 0 && (
        <div className="mb-6"><SectionHeader label="Recent Interactions" count={person.interactions.length} /><div className="space-y-2">{person.interactions.slice(0, 20).map((ix: InteractionSchema) => (
          <div key={ix.id} className="flex items-start gap-2 py-1"><MessageCircle className="w-3 h-3 text-text-quaternary shrink-0 mt-0.5" /><div className="flex-1 min-w-0"><div className="flex items-center gap-2"><Tag label={ix.channel} color={ix.direction === 'inbound' ? 'blue' : 'green'} /><span className="text-[10px] text-text-quaternary">{timeAgo(ix.timestamp)}</span></div>{ix.summary && <p className="text-[11px] text-text-tertiary mt-0.5 line-clamp-2">{ix.summary}</p>}</div></div>
        ))}</div></div>
      )}
    </div>
  );
}

function SurfacesPanel() {
  const { data, isLoading } = usePersonSurfaces();
  if (isLoading) return <SkeletonRows count={3} />;
  if (!data || data.items.length === 0) return <p className="text-[11px] text-text-quaternary text-center py-6">No surfaces right now.</p>;
  return (
    <div className="space-y-2">{data.items.map((item: PersonSurfaceItem, i: number) => (
      <div key={i} className="flex items-center gap-3 px-4 py-2 rounded-[7px] bg-bg-secondary border border-border">
        <div className="w-7 h-7 rounded-full bg-bg-tertiary flex items-center justify-center text-[10px] font-[590] text-text-secondary shrink-0">{item.person.name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()}</div>
        <div className="flex-1 min-w-0"><span className="text-[12px] font-[510] text-text-secondary block truncate">{item.person.name}</span><span className="text-[10px] text-text-quaternary truncate block">{item.reason}</span></div>
      </div>
    ))}</div>
  );
}

export default function PeoplePage() {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<'list' | 'surfaces'>('list');
  const { data, isLoading, isError } = usePeople(searchQuery || undefined);
  const people = data?.people ?? [];

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="shrink-0 px-5 md:px-8 py-4 md:py-6 border-b border-border">
        <div className="flex items-center justify-between mb-4">
          <h1 className="type-title">People</h1>
          <TabBar tabs={[{ id: 'list', label: 'Contacts' }, { id: 'surfaces', label: 'Surfaces' }]} active={tab} onChange={(id) => setTab(id as 'list' | 'surfaces')} />
        </div>
        {tab === 'list' && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-sm bg-bg-secondary border border-border-secondary">
            <Search className="w-3.5 h-3.5 text-text-quaternary shrink-0" />
            <input type="text" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="Search people..." className="flex-1 bg-transparent text-[13px] text-text placeholder:text-text-quaternary outline-none" />
            {searchQuery && <button onClick={() => setSearchQuery('')} className="p-0.5"><X className="w-3 h-3 text-text-quaternary" /></button>}
          </div>
        )}
      </div>
      {isError && <div className="px-5 md:px-8 pt-4"><ErrorBanner /></div>}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {tab === 'surfaces' ? (
          <div className="flex-1 overflow-y-auto p-5 md:p-8"><SurfacesPanel /></div>
        ) : (
          <>
            <div className="flex-1 overflow-y-auto">
              {isLoading ? <div className="p-4"><SkeletonRows count={8} /></div> : people.length === 0 ? (
                <EmptyState icon={<Users />} title="No people found" description={searchQuery ? 'Try a different search.' : 'People will appear here as you interact.'} />
              ) : (
                <div className="divide-y divide-border">{people.map((p) => <PersonRow key={p.id} person={p} active={selectedId === p.id} onClick={() => setSelectedId(p.id)} />)}</div>
              )}
            </div>
            {selectedId && <div className="w-full lg:w-[400px] shrink-0 border-t lg:border-t-0 lg:border-l border-border bg-bg-panel overflow-y-auto fixed inset-0 lg:static z-50 lg:z-auto"><PersonDetail id={selectedId} onClose={() => setSelectedId(null)} /></div>}
          </>
        )}
      </div>
    </div>
  );
}
