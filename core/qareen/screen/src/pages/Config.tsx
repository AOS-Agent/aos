import { useState } from 'react';
import { Settings, User, Clock, Shield, Key, Plug } from 'lucide-react';
import { useOperator, useUpdateOperator, useAccounts, useIntegrations } from '@/hooks/useConfig';
import { EmptyState, Tag, StatusDot, TabBar, SectionHeader, Skeleton, SkeletonRows, ErrorBanner, Button, Input } from '@/components/primitives';

function ProfileSection() {
  const { data: op, isLoading } = useOperator();
  const updateOp = useUpdateOperator();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState('');
  const [tz, setTz] = useState('');

  if (isLoading || !op) return <SkeletonRows count={4} />;

  const startEdit = () => { setName(op.name); setTz(op.timezone || ''); setEditing(true); };
  const save = () => { updateOp.mutate({ name: name || undefined, timezone: tz || undefined }); setEditing(false); };

  return (
    <div className="space-y-4">
      <SectionHeader label="Profile" icon={<User />} action={!editing ? <button onClick={startEdit} className="text-[11px] text-accent hover:text-accent-hover">Edit</button> : undefined} />
      {editing ? (
        <div className="space-y-3 max-w-md">
          <Input label="Name" value={name} onChange={e => setName(e.target.value)} />
          <Input label="Timezone" value={tz} onChange={e => setTz(e.target.value)} placeholder="e.g. Asia/Riyadh" />
          <div className="flex gap-2"><Button variant="primary" size="sm" onClick={save}>Save</Button><Button variant="ghost" size="sm" onClick={() => setEditing(false)}>Cancel</Button></div>
        </div>
      ) : (
        <div className="space-y-2">
          <div className="flex items-center justify-between py-1"><span className="text-[11px] text-text-quaternary">Name</span><span className="text-[13px] text-text-secondary">{op.name}</span></div>
          {op.handle && <div className="flex items-center justify-between py-1"><span className="text-[11px] text-text-quaternary">Handle</span><span className="text-[13px] text-text-secondary">@{op.handle}</span></div>}
          {op.email && <div className="flex items-center justify-between py-1"><span className="text-[11px] text-text-quaternary">Email</span><span className="text-[13px] text-text-secondary">{op.email}</span></div>}
          {op.timezone && <div className="flex items-center justify-between py-1"><span className="text-[11px] text-text-quaternary">Timezone</span><span className="text-[13px] text-text-secondary">{op.timezone}</span></div>}
          {op.locale && <div className="flex items-center justify-between py-1"><span className="text-[11px] text-text-quaternary">Locale</span><span className="text-[13px] text-text-secondary">{op.locale}</span></div>}
        </div>
      )}
    </div>
  );
}

function ScheduleSection() {
  const { data: op, isLoading } = useOperator();
  if (isLoading || !op) return <SkeletonRows count={3} />;
  const prefs = op.preferences || {};
  return (
    <div className="space-y-4">
      <SectionHeader label="Schedule" icon={<Clock />} />
      <div className="space-y-2">
        <div className="flex items-center justify-between py-1"><span className="text-[11px] text-text-quaternary">Morning briefing</span><span className="text-[13px] text-text-secondary">{prefs.morning_briefing_time || 'Not set'}</span></div>
        <div className="flex items-center justify-between py-1"><span className="text-[11px] text-text-quaternary">Evening checkin</span><span className="text-[13px] text-text-secondary">{prefs.evening_checkin_time || 'Not set'}</span></div>
        <div className="flex items-center justify-between py-1"><span className="text-[11px] text-text-quaternary">Quiet hours</span><span className="text-[13px] text-text-secondary">{prefs.quiet_hours || 'Not set'}</span></div>
      </div>
    </div>
  );
}

function TrustSection() {
  const { data: op, isLoading } = useOperator();
  if (isLoading || !op) return <SkeletonRows count={2} />;
  const prefs = op.preferences || {};
  return (
    <div className="space-y-4">
      <SectionHeader label="Trust Defaults" icon={<Shield />} />
      <div className="flex items-center justify-between py-1"><span className="text-[11px] text-text-quaternary">Default trust level</span><Tag label={`Level ${prefs.default_trust_level ?? 3}`} color="blue" /></div>
    </div>
  );
}

function AccountsSection() {
  const { data, isLoading, isError } = useAccounts();
  if (isError) return <p className="text-[11px] text-red">Failed to load accounts.</p>;
  if (isLoading) return <SkeletonRows count={3} />;
  // API may return accounts as array or Record — handle both
  const accounts = data?.accounts;
  const accountEntries: [string, unknown][] = Array.isArray(accounts)
    ? accounts.map((a: any) => [a.name ?? a.id ?? 'unknown', a])
    : accounts && typeof accounts === 'object'
      ? Object.entries(accounts)
      : [];
  if (accountEntries.length === 0) return <p className="text-[11px] text-text-quaternary">No accounts configured.</p>;
  return (
    <div className="space-y-4">
      <SectionHeader label="Accounts" icon={<Key />} />
      <div className="space-y-1">
        {accountEntries.map(([name, val]) => (
          <div key={name} className="flex items-center justify-between py-2 px-3 rounded-[7px] bg-bg-secondary border border-border">
            <span className="text-[13px] font-[510] text-text-secondary capitalize">{name}</span>
            <span className="text-[11px] text-text-quaternary font-mono">{typeof val === 'object' ? 'configured' : '\u2022\u2022\u2022\u2022'}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function IntegrationsSection() {
  const { data, isLoading, isError } = useIntegrations();
  if (isError) return <p className="text-[11px] text-red">Failed to load integrations.</p>;
  if (isLoading) return <SkeletonRows count={3} />;
  const integrations = Array.isArray(data?.integrations) ? data.integrations : [];
  if (integrations.length === 0) return <p className="text-[11px] text-text-quaternary">No integrations configured.</p>;
  return (
    <div className="space-y-4">
      <SectionHeader label="Integrations" icon={<Plug />} />
      <div className="space-y-1">
        {integrations.map((intg: any) => {
          const intgName = intg.name ?? intg.id ?? 'unknown';
          const isActive = intg.is_active ?? intg.status === 'active';
          const isError = intg.is_healthy === false || intg.status === 'error';
          const intgType = intg.type ?? intg.category ?? '';
          return (
            <div key={intgName} className="flex items-center justify-between py-2 px-3 rounded-[7px] bg-bg-secondary border border-border">
              <div className="flex items-center gap-2">
                <StatusDot color={isActive ? 'green' : isError ? 'red' : 'gray'} size="md" />
                <span className="text-[13px] font-[510] text-text-secondary">{intgName}</span>
              </div>
              <div className="flex items-center gap-2">
                {intgType && <Tag label={intgType} color="gray" />}
                <Tag label={isActive ? 'Active' : 'Inactive'} color={isActive ? 'green' : 'gray'} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function ConfigPage() {
  const [tab, setTab] = useState('profile');
  return (
    <div className="px-5 md:px-8 py-4 md:py-6 overflow-y-auto h-full">
      <h1 className="type-title mb-6">Settings</h1>
      <TabBar tabs={[{ id: 'profile', label: 'Profile' }, { id: 'schedule', label: 'Schedule' }, { id: 'trust', label: 'Trust' }, { id: 'accounts', label: 'Accounts' }, { id: 'integrations', label: 'Integrations' }]} active={tab} onChange={setTab} className="mb-6" />
      <div className="max-w-2xl">
        {tab === 'profile' && <ProfileSection />}
        {tab === 'schedule' && <ScheduleSection />}
        {tab === 'trust' && <TrustSection />}
        {tab === 'accounts' && <AccountsSection />}
        {tab === 'integrations' && <IntegrationsSection />}
      </div>
    </div>
  );
}
