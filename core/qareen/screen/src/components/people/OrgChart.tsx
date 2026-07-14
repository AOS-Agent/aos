import { useState } from 'react';
import { Building2, Users } from 'lucide-react';
import { useOrgs, useOrgDetail } from '@/hooks/usePeople';
import { Tag } from '@/components/primitives/Tag';
import { SectionHeader } from '@/components/primitives/SectionHeader';
import { EmptyState } from '@/components/primitives/EmptyState';
import { Skeleton, SkeletonRows } from '@/components/primitives/Skeleton';
import type { OrgResponse, OrgMemberResponse } from '@/lib/types';

// ---------------------------------------------------------------------------
// Seniority dot color
// ---------------------------------------------------------------------------

const SENIORITY_COLORS: Record<string, string> = {
  ceo: 'bg-accent',
  founder: 'bg-accent',
  cto: 'bg-blue',
  coo: 'bg-blue',
  cfo: 'bg-blue',
  vp: 'bg-purple',
  'vice president': 'bg-purple',
  director: 'bg-purple',
  manager: 'bg-teal',
  lead: 'bg-teal',
  senior: 'bg-green',
  engineer: 'bg-text-tertiary',
  designer: 'bg-text-tertiary',
  analyst: 'bg-text-tertiary',
};

function seniorityDot(role?: string): string {
  if (!role) return 'bg-text-quaternary';
  const lower = role.toLowerCase();
  for (const [key, color] of Object.entries(SENIORITY_COLORS)) {
    if (lower.includes(key)) return color;
  }
  return 'bg-text-quaternary';
}

// ---------------------------------------------------------------------------
// Org list item
// ---------------------------------------------------------------------------

function OrgListItem({ org, isActive, onClick }: {
  org: OrgResponse;
  isActive: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`group w-full text-left flex items-center gap-3 px-3 py-2.5 rounded-[5px] transition-all cursor-pointer ${
        isActive ? 'bg-selected' : 'hover:bg-hover'
      }`}
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      <div className="w-8 h-8 rounded-full bg-bg-tertiary border border-border-secondary flex items-center justify-center shrink-0">
        <Building2 className="w-3.5 h-3.5 text-text-quaternary" />
      </div>
      <div className="flex-1 min-w-0">
        <span className="text-[13px] font-[510] text-text-secondary truncate block group-hover:text-text transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
          {org.name}
        </span>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="text-[11px] text-text-quaternary tabular-nums">{org.member_count} member{org.member_count !== 1 ? 's' : ''}</span>
          {org.domain && (
            <Tag label={org.domain} color="blue" size="sm" />
          )}
        </div>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Member row
// ---------------------------------------------------------------------------

function MemberRow({ member, onSelect }: { member: OrgMemberResponse; onSelect: (id: string) => void }) {
  const dotColor = seniorityDot(member.role);

  return (
    <button
      onClick={() => onSelect(member.person_id)}
      className="group w-full text-left flex items-center gap-3 px-3 py-2 rounded-[5px] hover:bg-hover transition-colors cursor-pointer"
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      <span className={`w-2 h-2 rounded-full shrink-0 ${dotColor}`} />
      <div className="flex-1 min-w-0">
        <span className="text-[12px] font-[510] text-text-secondary truncate block group-hover:text-text transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
          {member.name}
        </span>
      </div>
      {member.role && (
        <span className="text-[11px] text-text-quaternary shrink-0 truncate max-w-[120px]">{member.role}</span>
      )}
      {member.department && (
        <Tag label={member.department} color="gray" size="sm" />
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Org Detail
// ---------------------------------------------------------------------------

function OrgDetail({ id, onSelect }: { id: string; onSelect: (personId: string) => void }) {
  const { data, isLoading } = useOrgDetail(id);
  const org = data?.organization;
  const members = data?.members ?? [];

  if (isLoading) {
    return (
      <div className="p-4 space-y-3">
        <Skeleton className="h-5 w-40" />
        <SkeletonRows count={5} />
      </div>
    );
  }

  if (!org) return null;

  // Sort by importance (lower = more important)
  const sorted = [...members].sort((a, b) => a.importance - b.importance);

  return (
    <div>
      {/* Header */}
      <div className="mb-4">
        <h3 className="text-[16px] font-[600] text-text tracking-[-0.01em] mb-1">{org.name}</h3>
        <div className="flex items-center gap-2 flex-wrap">
          {org.type && <Tag label={org.type} color="purple" size="sm" />}
          {org.industry && <Tag label={org.industry} color="blue" size="sm" />}
          {org.city && <Tag label={org.city} color="gray" size="sm" />}
          {org.domain && (
            <span className="text-[11px] text-text-quaternary">{org.domain}</span>
          )}
        </div>
      </div>

      {/* Members */}
      <SectionHeader label="Members" count={members.length} />
      {sorted.length === 0 ? (
        <p className="text-[11px] text-text-quaternary py-4">No members found</p>
      ) : (
        <div className="space-y-px">
          {sorted.map(m => (
            <MemberRow key={m.person_id} member={m} onSelect={onSelect} />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// OrgChart — split layout
// ---------------------------------------------------------------------------

export default function OrgChart({ onSelect }: { onSelect: (personId: string) => void }) {
  const { data, isLoading } = useOrgs();
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(null);

  const orgs = data?.organizations ?? [];

  if (isLoading) {
    return (
      <div className="flex gap-4">
        <div className="w-1/3 space-y-2">
          {[1, 2, 3, 4].map(i => <Skeleton key={i} className="h-14 rounded-[5px]" />)}
        </div>
        <div className="flex-1">
          <SkeletonRows count={6} />
        </div>
      </div>
    );
  }

  if (orgs.length === 0) {
    return (
      <EmptyState
        icon={<Building2 />}
        title="No organizations yet"
        description="Organizations are detected from your contacts' metadata — company, domain, and role information."
      />
    );
  }

  return (
    <div className="flex gap-4 min-h-[400px]">
      {/* Left: org list */}
      <div className="w-[280px] shrink-0">
        <SectionHeader label="Organizations" count={orgs.length} />
        <div className="space-y-px">
          {orgs.map(org => (
            <OrgListItem
              key={org.id}
              org={org}
              isActive={selectedOrgId === org.id}
              onClick={() => setSelectedOrgId(org.id)}
            />
          ))}
        </div>
      </div>

      {/* Right: detail */}
      <div className="flex-1 border-l border-border pl-4">
        {selectedOrgId ? (
          <OrgDetail id={selectedOrgId} onSelect={onSelect} />
        ) : (
          <div className="flex items-center justify-center h-full">
            <p className="text-[13px] text-text-quaternary">Select an organization to see its members</p>
          </div>
        )}
      </div>
    </div>
  );
}
